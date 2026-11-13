"""Edit multiple multiline guidance phrases for one stable answer option."""

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk


class GuidanceDialog(Gtk.Window):
    """Keep a private draft so cancellation never changes the saved option."""

    def __init__(self, parent, option, on_saved=lambda: None):
        super().__init__(
            transient_for=parent,
            modal=True,
            title="回答后的提醒与引导",
            default_width=540,
            default_height=440,
        )
        self.option = option
        self.on_saved = on_saved
        self.rows = []
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        for side in ("start", "end", "top", "bottom"):
            getattr(root, "set_margin_" + side)(12)
        self.set_child(root)
        root.append(
            Gtk.Label(
                label=f"回答“{option['label']}”后随机显示一条；每条可换行。\n没有标语时不显示引导。",
                wrap=True,
                xalign=0,
            )
        )
        scroll = Gtk.ScrolledWindow(
            vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER
        )
        self.list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        scroll.set_child(self.list_box)
        root.append(scroll)
        add = Gtk.Button(label="添加标语")
        add.connect("clicked", lambda button: self.add_phrase())
        root.append(add)
        actions = Gtk.Box(spacing=8, halign=Gtk.Align.END)
        cancel = Gtk.Button(label="取消")
        cancel.connect("clicked", lambda button: self.destroy())
        save = Gtk.Button(label="保存")
        save.connect("clicked", lambda button: self.save())
        actions.append(cancel)
        actions.append(save)
        root.append(actions)
        for text in option.get("guidance", []):
            self.add_phrase(text)

    def add_phrase(self, text=""):
        row = Gtk.Box(spacing=8)
        view = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, hexpand=True)
        view.get_buffer().set_text(text)
        scroll = Gtk.ScrolledWindow(min_content_height=80, hexpand=True)
        scroll.set_child(view)
        row.append(scroll)
        remove = Gtk.Button(label="删除", valign=Gtk.Align.START)
        remove.connect("clicked", lambda button: self.remove_phrase(row))
        row.append(remove)
        self.rows.append((row, view))
        self.list_box.append(row)

    def remove_phrase(self, row):
        self.rows = [(box, view) for box, view in self.rows if box is not row]
        self.list_box.remove(row)

    def save(self):
        phrases = []
        for row, view in self.rows:
            buffer = view.get_buffer()
            text = buffer.get_text(
                buffer.get_start_iter(), buffer.get_end_iter(), True
            ).strip()
            if text:
                phrases.append(text)
        self.option["guidance"] = phrases
        self.on_saved()
        self.destroy()
