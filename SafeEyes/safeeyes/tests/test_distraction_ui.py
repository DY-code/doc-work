"""Exercise real GTK controls in hidden windows on an explicitly enabled display."""

import os
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from safeeyes.configuration import Config
from safeeyes.distraction import DistractionPrompt, DistractionStore
from safeeyes.ui.break_screen import BreakScreen, BreakScreenWindow
from safeeyes.ui.settings_dialog import BreakSettingsDialog

pytestmark = pytest.mark.skipif(
    os.environ.get("SAFE_EYES_GTK_TESTS") != "1",
    reason="Set SAFE_EYES_GTK_TESTS=1 with access to a GTK display",
)


def test_multi_monitor_buttons_update_together_without_touching_countdown(tmp_path):
    prompt = DistractionPrompt(DistractionStore(tmp_path / "stats.json"))
    prompt.begin("break-1", True)
    screen = SimpleNamespace(distraction=prompt, windows=[])
    screen._refresh_distraction = lambda: [
        window.refresh_distraction() for window in screen.windows
    ]

    def callback(token, answer):
        BreakScreen._answer_distraction(screen, token, answer)

    try:
        for _ in range(2):
            window = BreakScreenWindow(
                None,
                "你分心了吗？",
                None,
                "",
                1280,
                720,
                [],
                Mock(),
                False,
                Mock(),
                True,
                Mock(),
                False,
                100,
                prompt,
                callback,
            )
            screen.windows.append(window)
        first, second = screen.windows
        assert first.box_distraction.get_visible()
        first._answer_buttons[0].emit("clicked")
        assert prompt.store.today() == (1, 0)
        for window in screen.windows:
            assert all(not button.get_sensitive() for button in window._answer_buttons)
            assert "已记录：是" in window._answer_label.get_text()
            assert "100%" in window._summary_label.get_text()
            window.set_count_down("00:10", True)
            assert all(not button.get_sensitive() for button in window._answer_buttons)
            assert window.lbl_count.get_text() == "00:10"
        second._answer_buttons[1].emit("clicked")
        assert prompt.store.today() == (1, 0)
    finally:
        for window in screen.windows:
            window.destroy()


def test_disabled_item_has_no_statistics_controls(tmp_path):
    prompt = DistractionPrompt(DistractionStore(tmp_path / "stats.json"))
    prompt.begin("break-1", False)
    window = BreakScreenWindow(
        None,
        "喝点水吧",
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
        assert not window.box_distraction.get_visible()
        assert not window._answer_buttons
    finally:
        window.destroy()


def test_settings_switch_survives_rename_and_can_be_disabled():
    item = {"name": "你分心了吗？", "distraction_stats": True}
    config = Config(
        {
            "short_breaks": [item],
            "long_breaks": [],
            "short_break_interval": 20,
            "short_break_duration": 30,
        },
        {},
    )
    parent = Gtk.Window()
    dialog = BreakSettingsDialog(parent, item, True, config, {}, Mock(), Mock(), Mock())
    try:
        assert dialog.dropdown_question_type.get_selected() == 1
        dialog.txt_break.set_text("刚才是否走神？")
        dialog.on_window_delete()
        assert item["name"] == "刚才是否走神？"
        assert item["distraction_stats"] is True
        dialog = BreakSettingsDialog(
            parent, item, True, config, {}, Mock(), Mock(), Mock()
        )
        dialog.dropdown_question_type.set_selected(0)
        dialog.on_window_delete()
        assert item["distraction_stats"] is False
    finally:
        dialog.destroy()
        parent.destroy()


def test_away_threshold_setting_defaults_to_two_hours_and_saves_custom_minutes(
    monkeypatch,
):
    from safeeyes import utility
    from safeeyes.ui.settings_dialog import SettingsDialog

    data = utility.load_json(utility.SYSTEM_CONFIG_FILE_PATH)
    config = Config(data, {})
    monkeypatch.setattr(utility, "load_plugins_config", lambda config: [])
    saved = Mock()
    dialog = SettingsDialog(None, config, saved)
    try:
        assert dialog.spin_away_reset.get_value_as_int() == 120
        dialog.spin_away_reset.set_value(45)
        dialog.on_window_delete()
        assert config.get("away_reset_minutes") == 45
        saved.assert_called_once_with(config)
    finally:
        dialog.destroy()


def test_survey_options_editor_preserves_ids_and_deleted_choices():
    from safeeyes.ui.survey_options import SurveyOptionsEditor

    config = Config({}, {})
    editor = SurveyOptionsEditor(config)
    editor.selector.set_selected(2)  # Body tension
    original = editor.rows[0][0]["id"]
    editor.rows[0][1].set_text("身体放松")
    editor.add_option()
    added = editor.rows[-1][0]
    editor.rows[-1][1].set_text("轻度紧绷")
    editor.move_option(added, -1)
    assert editor.save()
    choices = config.get("survey_options")["tension"]
    assert choices[0]["id"] == original
    assert choices[0]["label"] == "身体放松"
    assert choices[1]["id"] == added["id"]
    editor.remove_option(added)
    assert editor.save()
    assert (
        next(
            item
            for item in config.get("survey_options")["tension"]
            if item["id"] == added["id"]
        )["active"]
        is False
    )
    editor.remove_option(editor.rows[0][0])
    assert len(editor.rows) == 2
    assert editor.error.get_text()


def test_break_question_selector_is_exclusive():
    item = {"name": "自定义提醒", "question_type": "posture"}
    config = Config(
        {
            "short_breaks": [item],
            "long_breaks": [],
            "short_break_interval": 20,
            "short_break_duration": 30,
        },
        {},
    )
    parent = Gtk.Window()
    dialog = BreakSettingsDialog(parent, item, True, config, {}, Mock(), Mock(), Mock())
    try:
        assert dialog.dropdown_question_type.get_selected() == 2
        dialog.dropdown_question_type.set_selected(4)
        dialog.on_window_delete()
        assert item["question_type"] == "fatigue"
        assert item["distraction_stats"] is False
    finally:
        dialog.destroy()
        parent.destroy()


def test_categorical_buttons_support_many_levels_and_one_shared_answer(tmp_path):
    from safeeyes.surveys import SurveyPrompt, SurveyStore, default_options

    catalog = default_options()
    catalog["fatigue"].extend(
        [
            {"id": "mild", "label": "轻度疲劳", "active": True},
            {"id": "severe", "label": "明显疲劳", "active": True},
        ]
    )
    prompt = SurveyPrompt(SurveyStore(tmp_path / "stats.json"))
    prompt.configure(catalog)
    prompt.begin("one", True, "fatigue")
    windows = []

    def answer(token, option):
        prompt.submit(token, option)
        for window in windows:
            window.refresh_distraction()

    try:
        for _ in range(2):
            windows.append(
                BreakScreenWindow(
                    None,
                    "检查状态",
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
                    answer,
                )
            )
        assert len(windows[0]._answer_buttons) == 4
        windows[0]._answer_buttons[2].emit("clicked")
        for window in windows:
            assert "轻度疲劳" in window._answer_label.get_text()
            assert "100%" in window._summary_label.get_text()
            assert all(not button.get_sensitive() for button in window._answer_buttons)
        windows[1]._answer_buttons[1].emit("clicked")
        assert prompt.store.today("fatigue") == {"mild": 1}
    finally:
        for window in windows:
            window.destroy()
