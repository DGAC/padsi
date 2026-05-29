#!/usr/bin/python3

#
# Copyright (c) 2026 DGAC/DSNA
#
# This file is part of PADSI.
#
# This software is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This software is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this software.  If not, see <http://www.gnu.org/licenses/>.
#

import sys
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gtk, Gdk, GLib, Gio


class ClipboardTestWindow(Gtk.ApplicationWindow):
    def __init__(self, app):
        try:
            zone=sys.argv[1]
        except IndexError:
            raise Exception("No zone specified")
        super().__init__(application=app, title=f"Clipboard Test — zone '{zone}'")
        self.set_default_size(480, 360)
        self.set_resizable(True)

        # Top layout
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.set_child(root)

        # Header bar
        header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        header.set_margin_top(24)
        header.set_margin_bottom(8)
        header.set_margin_start(24)
        header.set_margin_end(24)

        title_label = Gtk.Label(label=f"Clipboard Test — zone '{zone}'")
        title_label.add_css_class("title-1")
        title_label.set_halign(Gtk.Align.START)

        display = Gdk.Display.get_default()
        backend = type(display).__name__

        subtitle = Gtk.Label(label=f"Display backend: {backend}")
        subtitle.add_css_class("dim-label")
        subtitle.set_halign(Gtk.Align.START)


        header.append(title_label)
        header.append(subtitle)
        root.append(header)

        sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        root.append(sep)

        #  Main content
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        content.set_margin_top(20)
        content.set_margin_bottom(20)
        content.set_margin_start(24)
        content.set_margin_end(24)
        root.append(content)

        #  Copy features
        copy_label = Gtk.Label(label="Text to copy")
        copy_label.add_css_class("heading")
        copy_label.set_halign(Gtk.Align.START)
        content.append(copy_label)

        copy_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        content.append(copy_row)

        self.entry = Gtk.Entry()
        self.entry.set_text(f"From '{zone}'")
        self.entry.set_hexpand(True)
        self.entry.connect("activate", self._on_copy)
        copy_row.append(self.entry)

        copy_btn = Gtk.Button(label="Copy")
        copy_btn.add_css_class("suggested-action")
        copy_btn.connect("clicked", self._on_copy)
        copy_row.append(copy_btn)

        # Paste features
        paste_label = Gtk.Label(label="Paste result")
        paste_label.add_css_class("heading")
        paste_label.set_halign(Gtk.Align.START)
        content.append(paste_label)

        paste_btn = Gtk.Button(label="Paste from clipboard")
        paste_btn.set_halign(Gtk.Align.START)
        paste_btn.connect("clicked", self._on_paste)
        content.append(paste_btn)

        # Scrolled text view for the pasted text
        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        content.append(scroll)

        self.result_view = Gtk.TextView()
        self.result_view.set_editable(True)
        self.result_view.set_cursor_visible(False)
        self.result_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.result_view.add_css_class("card")
        self.result_view.set_top_margin(8)
        self.result_view.set_bottom_margin(8)
        self.result_view.set_left_margin(10)
        self.result_view.set_right_margin(10)
        scroll.set_child(self.result_view)

        self.result_buf = self.result_view.get_buffer()
        self._set_result("(nothing pasted yet)")

        # Status bar
        sep2 = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        root.append(sep2)

        self.status_label = Gtk.Label(label="Ready")
        self.status_label.set_halign(Gtk.Align.START)
        self.status_label.set_margin_top(6)
        self.status_label.set_margin_bottom(6)
        self.status_label.set_margin_start(24)
        self.status_label.set_margin_end(24)
        self.status_label.add_css_class("dim-label")
        root.append(self.status_label)

        self.clipboard = display.get_clipboard()

    def _set_result(self, text: str):
        self.result_buf.set_text(text, -1)

    def _set_status(self, msg: str, temporary: bool = False):
        self.status_label.set_text(msg)
        if temporary:
            GLib.timeout_add_seconds(3, self._reset_status)

    def _reset_status(self):
        display = Gdk.Display.get_default()
        self._set_status(f"Ready")
        return GLib.SOURCE_REMOVE

    def _on_copy(self, *_):
        text = self.entry.get_text()
        if not text:
            self._set_status("⚠  Nothing to copy — enter some text first", temporary=True)
            return
        self.clipboard.set(text)
        self._set_status(f"✓  Copied {len(text)} character(s) to clipboard", temporary=True)

    def _on_paste(self, *_):
        self._set_status("Reading clipboard…")
        self.clipboard.read_text_async(None, self._on_paste_done)

    def _on_paste_done(self, clipboard, result):
        try:
            text = clipboard.read_text_finish(result)
            if text is None:
                self._set_result("(clipboard is empty or contains non-text data)")
                self._set_status("⚠  Clipboard had no text content", temporary=True)
            else:
                self._set_result(text)
                self._set_status(
                    f"✓  Pasted {len(text)} character(s) from clipboard",
                    temporary=True,
                )
        except GLib.Error as exc:
            self._set_result(f"Error reading clipboard:\n{exc.message}")
            self._set_status(f"✗  Error: {exc.message}", temporary=True)


class ClipboardTestApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="dev.example.ClipboardTest", flags=Gio.ApplicationFlags.NON_UNIQUE)

    def do_activate(self):
        win = ClipboardTestWindow(self)
        win.present()


if __name__ == "__main__":
    import sys
    app = ClipboardTestApp()
    sys.exit(app.run())
