#
# Copyright (c) 2025-2026 DGAC/DSNA
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

import os
import textwrap

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import (GdkPixbuf, GLib,  # noqa: E402 # pyright: ignore
                           GObject, Gtk)


class VMStartupWidget(Gtk.Box):
    """Widget to show statup information about a VM
    It also allows the user to request cancelling the VM startup, or request showing the VM's console
    """
    __gsignals__ = {
        "show-console-requested": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "cancel-requested": (GObject.SignalFlags.RUN_FIRST, None, ())
    }

    def __init__(self, vm_nickname:str|None, vm_descr:str, vm_usage:str, icon_path:str, spice_socket:str, *args, **kwargs):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10, *args, **kwargs)

        self._spice_socket=spice_socket

        hbox=Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=20)
        self.pack_start(hbox, expand=False, fill=False, padding=5)

        label=Gtk.Label(label="")
        hbox.pack_start(label, expand=True, fill=False, padding=5)

        pix=GdkPixbuf.Pixbuf.new_from_file_at_scale(icon_path, width=96, height=96, preserve_aspect_ratio=True)
        img=Gtk.Image()
        img.set_from_pixbuf(pix)
        hbox.add(img)

        label=Gtk.Label(label="")
        vm_descr="\n".join(textwrap.wrap(vm_descr, 30))
        match vm_usage:
            case "run":
                if vm_nickname:
                    if vm_nickname=="default":
                        label.set_markup(f"<big><b>{vm_descr}</b></big>\n\n")
                    else:
                        label.set_markup(f"<big><b>{vm_descr}</b></big>\n\n'<i> {vm_nickname}</i> '")
                else:
                    label.set_markup(f"<big><b>{vm_descr}</b></big>\n\n(first usage preparations)")
            case "update":
                label.set_markup(f"<big><b>{vm_descr}</b></big>\n\n(OS update)")
            case "install":
                label.set_markup(f"<big><b>{vm_descr}</b></big>\n\n(OS install)")
            case _:
                label.set_markup(f"<big><b>{vm_descr}</b></big>\n\n(unknown usage)")

        label.set_xalign(0)
        #label.set_size_request(250, -1)
        hbox.add(label)

        label=Gtk.Label(label="")
        hbox.pack_start(label, expand=True, fill=False, padding=5)

        spacing=Gtk.Label(label="")
        self.add(spacing)

        spinner=Gtk.Spinner()
        spinner.set_size_request(64, 64)
        self.add(spinner)
        spinner.start()

        if vm_usage=="run" and vm_nickname is None:
            label=Gtk.Label(label="VM is being customized")
        else:
            label=Gtk.Label(label="VM is starting")
        self.add(label)

        spacing=Gtk.Label(label="")
        self.add(spacing)


        bbox=Gtk.Box(spacing=5)
        self.pack_start(bbox, expand=False, fill=False, padding=5)

        label=Gtk.Label(label="")
        bbox.pack_start(label, expand=True, fill=False, padding=5)

        button=Gtk.Button.new_with_label("Show screen")
        button.set_tooltip_text("Display the virtual machine's console")
        button.connect("clicked", self._show_console_cb)
        button.set_sensitive(False)
        self._show_console_button=button
        self._socket_timer=GLib.timeout_add(50, self._check_socket_present)
        bbox.add(button)

        button=Gtk.Button.new_with_label("Cancel")
        button.set_tooltip_text("Stop the virtual machine startup and discard it")
        button.connect("clicked", self._cancel_cb)
        bbox.add(button)

        label=Gtk.Label(label="")
        bbox.pack_start(label, expand=True, fill=False, padding=5)

        self.show_all()

    def _check_socket_present(self):
        if os.path.exists(self._spice_socket):
            self._show_console_button.set_sensitive(True)
            return False # remove timer
        return True # keep timer

    def _show_console_cb(self, button):
        self.emit("show-console-requested")

    def _cancel_cb(self, button):
        self.emit("cancel-requested")
