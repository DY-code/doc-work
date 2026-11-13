"""Edit shared survey choices while retaining identities for historical counts."""

import copy
import uuid

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from safeeyes.surveys import DEFAULTS, QUESTIONS, default_options, validate_options
from safeeyes.ui.guidance_dialog import GuidanceDialog


class SurveyOptionsEditor(Gtk.Box):
    """Settings page for adding, renaming, disabling and ordering answer choices."""

    def __init__(self, config):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.set_margin_start(12)
        self.set_margin_end(12)
        self.set_margin_top(12)
        self.set_margin_bottom(12)
        self.config = config
        self.catalog = copy.deepcopy(config.get("survey_options") or default_options())
        names = config.get("survey_questions") or QUESTIONS
        self.questions = {key: names.get(key, key) for key in self.catalog}
        self.archived = set(config.get("survey_archived") or [])
        self._kind_ids = self._active_ids()
        self.kind = self._kind_ids[0] if self._kind_ids else None
        self.rows = []
        self.append(
            Gtk.Label(
                label="同类询问共用选项；每次休息只能回答一次。\n改名保留统计，删除后旧记录标为已停用。",
                wrap=True,
                xalign=0,
            )
        )
        self.selector = Gtk.DropDown(
            model=Gtk.StringList.new([self.questions[key] for key in self._kind_ids]),
            hexpand=True,
        )
        self._selector_handler = self.selector.connect("notify::selected", self._select)
        self.project_actions = Gtk.Box(spacing=8)
        self.project_actions.append(self.selector)
        self.new_question_button = Gtk.Button(label="新增", tooltip_text="新增询问项目")
        self.new_question_button.connect("clicked", lambda button: self.add_question())
        self.project_actions.append(self.new_question_button)
        self.delete_question_button = Gtk.Button(
            label="删除", tooltip_text="删除当前询问项目"
        )
        self.delete_question_button.connect("clicked", self._confirm_delete)
        self.project_actions.append(self.delete_question_button)
        self.append(self.project_actions)
        self.name_entry = Gtk.Entry(text=self.questions.get(self.kind, ""))
        self.name_entry.set_placeholder_text("询问名称 / 问句")
        self._name_handler = self.name_entry.connect("changed", self._rename_question)
        self.append(self.name_entry)
        scroll = Gtk.ScrolledWindow(vexpand=True, min_content_height=200)
        self.answer_area = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=8, valign=Gtk.Align.START
        )
        self.list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.answer_area.append(self.list_box)
        scroll.set_child(self.answer_area)
        self.append(scroll)
        self.add_answer_button = Gtk.Button(
            label="添加回答选项", halign=Gtk.Align.START
        )
        self.add_answer_button.connect("clicked", lambda button: self.add_option())
        self.answer_area.append(self.add_answer_button)
        self.error = Gtk.Label(wrap=True, xalign=0)
        self.append(self.error)
        self._render()

    def _collect(self):
        for item, entry in self.rows:
            item["label"] = entry.get_text().strip()

    def _active_ids(self):
        keys = [key for key in DEFAULTS if key in self.questions]
        keys.extend(key for key in self.questions if key not in DEFAULTS)
        return [key for key in keys if key not in self.archived]

    def _refresh_questions(self):
        self._kind_ids = self._active_ids()
        if self.kind not in self._kind_ids:
            self.kind = self._kind_ids[0] if self._kind_ids else None
        with self.selector.handler_block(self._selector_handler):
            self.selector.set_model(
                Gtk.StringList.new(
                    [self.questions[key] or "未命名询问" for key in self._kind_ids]
                )
            )
            self.selector.set_selected(
                self._kind_ids.index(self.kind)
                if self.kind
                else Gtk.INVALID_LIST_POSITION
            )

    def _rename_question(self, entry):
        if self.kind is None:
            return
        self.questions[self.kind] = entry.get_text().strip()
        self._refresh_questions()

    def add_question(self):
        self._collect()
        self.error.set_text("")
        key = "custom_" + uuid.uuid4().hex
        self.questions[key] = "新询问"
        self.catalog[key] = copy.deepcopy(default_options()["distraction"])
        self.kind = key
        self._refresh_questions()
        with self.name_entry.handler_block(self._name_handler):
            self.name_entry.set_text(self.questions[key])
        self._render()
        self.name_entry.grab_focus()
        self.name_entry.select_region(0, -1)

    def _select(self, *args):
        if self.selector.get_selected() >= len(self._kind_ids):
            return
        self._collect()
        self.kind = self._kind_ids[self.selector.get_selected()]
        with self.name_entry.handler_block(self._name_handler):
            self.name_entry.set_text(self.questions[self.kind])
        self.error.set_text("")
        self._render()

    def _render(self):
        child = self.list_box.get_first_child()
        while child:
            self.list_box.remove(child)
            child = self.list_box.get_first_child()
        self.rows = []
        self.name_entry.set_sensitive(self.kind is not None)
        self.selector.set_sensitive(self.kind is not None)
        self.delete_question_button.set_sensitive(self.kind is not None)
        self.add_answer_button.set_sensitive(self.kind is not None)
        if self.kind is None:
            self.error.set_text("暂无询问项目，请点击“新增”。")
            return
        active = [item for item in self.catalog[self.kind] if item.get("active", True)]
        for index, item in enumerate(active):
            row = Gtk.Box(spacing=6)
            entry = Gtk.Entry(text=item["label"], hexpand=True)
            self.rows.append((item, entry))
            row.append(entry)
            guidance = Gtk.Button(label=f"引导标语（{len(item.get('guidance', []))}）")
            guidance.connect("clicked", self._edit_guidance, item)
            row.append(guidance)
            for label, action in (
                ("↑", lambda item=item: self.move_option(item, -1)),
                ("↓", lambda item=item: self.move_option(item, 1)),
                ("删除", lambda item=item: self.remove_option(item)),
            ):
                button = Gtk.Button(label=label)
                button.connect("clicked", lambda button, callback: callback(), action)
                if label == "↑":
                    button.set_sensitive(index > 0)
                elif label == "↓":
                    button.set_sensitive(index < len(active) - 1)
                row.append(button)
            self.list_box.append(row)

    def affected_breaks(self, kind):
        return [
            item
            for group in ("short_breaks", "long_breaks")
            for item in self.config.get(group, [])
            if item.get(
                "question_type",
                "distraction" if item.get("distraction_stats") else "none",
            )
            == kind
        ]

    def _confirm_delete(self, button):
        if self.kind is None:
            return
        self._collect()
        kind = self.kind
        dialog = QuestionDeleteDialog(
            self.get_root(),
            self.questions[kind],
            len(self.affected_breaks(kind)),
            lambda: self.archive_question(kind),
        )
        dialog.present()

    def archive_question(self, kind):
        """Unbind archived projects while retaining names, options and statistics."""
        if kind not in self.questions or kind in self.archived:
            return
        self._collect()
        for item in self.affected_breaks(kind):
            item["question_type"] = "none"
            item["distraction_stats"] = False
        self.archived.add(kind)
        self._refresh_questions()
        with self.name_entry.handler_block(self._name_handler):
            self.name_entry.set_text(self.questions.get(self.kind, ""))
        self.error.set_text("")
        self._render()

    def _edit_guidance(self, button, item):
        self._collect()

        def saved():
            button.set_label(f"引导标语（{len(item.get('guidance', []))}）")

        dialog = GuidanceDialog(self.get_root(), item, saved)
        dialog.present()

    def add_option(self):
        if self.kind is None:
            return
        self._collect()
        self.error.set_text("")
        self.catalog[self.kind].append(
            {"id": uuid.uuid4().hex, "label": "新选项", "active": True}
        )
        self._render()

    def remove_option(self, item):
        self._collect()
        if len(self.rows) <= 2:
            self.error.set_text("每类至少保留两个选项。")
            return
        item["active"] = False
        self.error.set_text("")
        self._render()

    def move_option(self, item, direction):
        self._collect()
        items = self.catalog[self.kind]
        active = [option for option in items if option.get("active", True)]
        target = active.index(item) + direction
        if 0 <= target < len(active):
            first, second = items.index(item), items.index(active[target])
            items[first], items[second] = items[second], items[first]
            self._render()

    def save(self):
        self._collect()
        try:
            validate_options(
                {
                    key: items
                    for key, items in self.catalog.items()
                    if key not in self.archived
                }
            )
            names = [
                name for key, name in self.questions.items() if key not in self.archived
            ]
            if not all(names) or len(set(names)) != len(names):
                raise ValueError("询问名称不能为空或重复。")
        except ValueError as error:
            self.error.set_text(str(error))
            return False
        self.config.set("survey_options", copy.deepcopy(self.catalog))
        self.config.set("survey_questions", dict(self.questions))
        self.config.set("survey_archived", sorted(self.archived))
        return True


class QuestionDeleteDialog(Gtk.Window):
    """Explain the actual binding impact before archiving a project."""

    def __init__(self, parent, name, count, on_confirm):
        super().__init__(transient_for=parent, modal=True, title="删除询问项目")
        self.on_confirm = on_confirm
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        for side in ("top", "bottom", "start", "end"):
            getattr(box, "set_margin_" + side)(16)
        self.message = Gtk.Label(
            label=(
                f"删除“{name}”？\n关联的 {count} 条休息将改为“无”。\n"
                "项目定义和统计数据保留，重启后不会自动恢复。"
            ),
            wrap=True,
            max_width_chars=40,
            xalign=0,
        )
        box.append(self.message)
        actions = Gtk.Box(spacing=8, halign=Gtk.Align.END)
        cancel = Gtk.Button(label="取消")
        cancel.connect("clicked", lambda button: self.destroy())
        confirm = Gtk.Button(label="删除")
        confirm.add_css_class("destructive-action")
        confirm.connect("clicked", lambda button: self.confirm())
        actions.append(cancel)
        actions.append(confirm)
        box.append(actions)
        self.set_child(box)

    def confirm(self):
        self.on_confirm()
        self.destroy()
