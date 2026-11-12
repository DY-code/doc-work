"""Edit shared survey choices while retaining identities for historical counts."""

import copy
import uuid

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from safeeyes.surveys import KINDS, TITLES, default_options, validate_options


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
        self.kind = "distraction"
        self.rows = []
        self.append(
            Gtk.Label(
                label="同类询问共用选项；每次休息只能回答一次。\n改名保留统计，删除后旧记录标为已停用。",
                wrap=True,
                xalign=0,
            )
        )
        self.selector = Gtk.DropDown(model=Gtk.StringList.new(list(TITLES[1:])))
        self.selector.connect("notify::selected", self._select)
        self.append(self.selector)
        scroll = Gtk.ScrolledWindow(vexpand=True, min_content_height=200)
        self.list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        scroll.set_child(self.list_box)
        self.append(scroll)
        add = Gtk.Button(label="添加选项")
        add.connect("clicked", lambda button: self.add_option())
        self.append(add)
        self.error = Gtk.Label(wrap=True, xalign=0)
        self.append(self.error)
        self._render()

    def _collect(self):
        for item, entry in self.rows:
            item["label"] = entry.get_text().strip()

    def _select(self, *args):
        self._collect()
        self.kind = KINDS[self.selector.get_selected() + 1]
        self.error.set_text("")
        self._render()

    def _render(self):
        child = self.list_box.get_first_child()
        while child:
            self.list_box.remove(child)
            child = self.list_box.get_first_child()
        self.rows = []
        active = [item for item in self.catalog[self.kind] if item.get("active", True)]
        for index, item in enumerate(active):
            row = Gtk.Box(spacing=6)
            entry = Gtk.Entry(text=item["label"], hexpand=True)
            self.rows.append((item, entry))
            row.append(entry)
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

    def add_option(self):
        self._collect()
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
            validate_options(self.catalog)
        except ValueError as error:
            self.error.set_text(str(error))
            return False
        self.config.set("survey_options", copy.deepcopy(self.catalog))
        return True
