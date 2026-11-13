"""Allocate real GTK layouts to catch overlap and inaccessible scrolling content."""

import os
from unittest.mock import Mock

import gi
import pytest

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, Gdk

from safeeyes import utility
from safeeyes.surveys import SurveyPrompt, SurveyStore, default_options
from safeeyes.ui.break_screen import BreakScreenWindow

pytestmark = pytest.mark.skipif(
    os.environ.get("SAFE_EYES_GTK_TESTS") != "1", reason="Requires a GTK display"
)


def bounds(widget, relative):
    ok, rect = widget.compute_bounds(relative)
    assert ok
    assert rect.get_width() > 0 and rect.get_height() > 0
    return rect


def intersects(first, second):
    return (
        first.get_x() < second.get_x() + second.get_width() - 1
        and second.get_x() < first.get_x() + first.get_width() - 1
        and first.get_y() < second.get_y() + second.get_height() - 1
        and second.get_y() < first.get_y() + first.get_height() - 1
    )


def test_short_buttons_are_compact_and_question_is_shown_verbatim(tmp_path):
    provider = Gtk.CssProvider()
    provider.load_from_path(utility.SYSTEM_STYLE_SHEET_PATH)
    display = Gdk.Display.get_default()
    Gtk.StyleContext.add_provider_for_display(display, provider, 600)
    prompt = SurveyPrompt(SurveyStore(tmp_path / "stats.json"))
    prompt.configure(default_options(), {"distraction": "你刚才走神了吗？"})
    prompt.begin("one", True, "distraction")
    window = BreakScreenWindow(
        None,
        "保持觉察",
        None,
        "",
        1280,
        720,
        [],
        Mock(),
        False,
        Mock(),
        False,
        Mock(),
        False,
        100,
        prompt,
        Mock(),
    )
    try:
        root = window.get_child()
        root.allocate(1280, 720, -1, None)
        assert window.box_distraction.get_first_child().get_text() == "你刚才走神了吗？"
        for button in window._answer_buttons:
            rect = bounds(button, root)
            assert 30 <= rect.get_height() <= 34
            assert rect.get_width() < 100
        assert bounds(window._answer_grid, root).get_width() < 220
    finally:
        window.destroy()
        Gtk.StyleContext.remove_provider_for_display(display, provider)


@pytest.mark.parametrize("width,height", [(320, 480), (640, 480), (1280, 720)])
@pytest.mark.parametrize("large_text", [False, True])
def test_long_survey_layout_has_no_overlaps_and_everything_is_scrollable(
    tmp_path, width, height, large_text
):
    provider = Gtk.CssProvider()
    provider.load_from_path(utility.SYSTEM_STYLE_SHEET_PATH)
    display = Gdk.Display.get_default()
    Gtk.StyleContext.add_provider_for_display(display, provider, 600)
    larger = Gtk.CssProvider()
    larger.load_from_data(
        b".lbl_message {font-size:32pt;} .survey_answer_text {font-size:18pt;}"
    )
    if large_text:
        Gtk.StyleContext.add_provider_for_display(display, larger, 601)
    prompt = SurveyPrompt(SurveyStore(tmp_path / "stats.json"))
    catalog = default_options()
    catalog["fatigue"] = [
        {
            "id": str(i),
            "label": "这是较长的疲劳程度描述，需要完整显示并自动换行" * 2 + str(i),
            "active": True,
            "guidance": [
                "放松身体并缓慢呼气，注意肩膀与下颌的紧绷。" * 20 + "\n保持自然呼吸。"
            ],
        }
        for i in range(8)
    ]
    prompt.configure(catalog)
    prompt.begin("one", True, "fatigue")
    for i in range(8):
        prompt.store.record("fatigue", str(i))
    window = BreakScreenWindow(
        None,
        "这是一个较长的休息提醒标语，请检查当前精神状态" * 3,
        None,
        "插件补充文字" * 20,
        width,
        height,
        [],
        Mock(),
        True,
        Mock(),
        True,
        Mock(),
        True,
        100,
        prompt,
        Mock(),
    )
    try:
        root = window.get_child()
        # Allocation exercises GTK's height-for-width measurement without mapping
        # a fullscreen window or using the application's keyboard-grab machinery.
        root.allocate(width, height, -1, None)
        for answered in (False, True):
            if answered:
                prompt.submit("one", "3")
                window.refresh_distraction()
                root.queue_resize()
                root.allocate(width, height, -1, None)
            sections = [
                window.lbl_message,
                window.box_distraction,
                window.lbl_count,
                window.box_buttons,
                window.lbl_widget,
            ]
            for first, second in zip(sections, sections[1:]):
                a, b = bounds(first, root), bounds(second, root)
                assert a.get_y() + a.get_height() + 12 <= b.get_y()
            buttons = [bounds(button, root) for button in window._answer_buttons]
            for index, first in enumerate(buttons):
                assert first.get_x() >= 0
                assert first.get_x() + first.get_width() <= width
                for second in buttons[index + 1 :]:
                    assert not intersects(first, second)
            summary = bounds(window._summary_label, root)
            assert all(not intersects(button, summary) for button in buttons)
            if answered:
                guidance = bounds(window._guidance_label, root)
                answer = bounds(window._answer_label, root)
                assert answer.get_y() + answer.get_height() <= guidance.get_y()
                assert guidance.get_y() + guidance.get_height() <= summary.get_y()
            for widget in sections:
                rect = bounds(widget, window.content_column)
                assert rect.get_y() >= 0
                assert (
                    rect.get_y() + rect.get_height()
                    <= window.content_column.get_height() + 1
                )
            adjustment = window.content_scroll.get_vadjustment()
            assert adjustment.get_upper() > adjustment.get_page_size()
            assert adjustment.get_upper() >= window.content_column.get_height()
    finally:
        window.destroy()
        Gtk.StyleContext.remove_provider_for_display(display, provider)
        if large_text:
            Gtk.StyleContext.remove_provider_for_display(display, larger)
