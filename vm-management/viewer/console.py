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

from __future__ import annotations

import json
import os
import syslog
from urllib import parse

import gi
import requests_unixsocket
from usb_devices import USBDevice, USBDeviceState, USBDeviceWidget

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, GObject, Gtk  # noqa: E402 # pyright: ignore

gi.require_version('Gdk', '3.0')
from gi.repository import Gdk, GdkPixbuf  # noqa: E402 # pyright: ignore

gi.require_version('SpiceClientGLib', '2.0')
from gi.repository import SpiceClientGLib  # noqa: E402 # pyright: ignore

gi.require_version('SpiceClientGtk', '3.0')
from gi.repository import SpiceClientGtk  # noqa: E402 # pyright: ignore

_debug=True

def _load_image_file(path:str, width:int=48, height:int=48) -> Gtk.Image:
    pix=GdkPixbuf.Pixbuf.new_from_file_at_scale(path, width=height, height=height, preserve_aspect_ratio=True)
    image=Gtk.Image()
    image.set_from_pixbuf(pix)
    return image

class VMActions(Gtk.Grid):
    """Top banner showing the different actions on the VM (fulscreen, devices management, and window close)"""
    __gsignals__ = {
        "fullscreen": (GObject.SignalFlags.RUN_FIRST, None, (bool,)),
        "console-hide": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "discard": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "shutdown": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "error": (GObject.SignalFlags.RUN_FIRST, None, (str, str))
    }

    def __init__(self, vm_widget:VMConsoleWidget):
        Gtk.Grid.__init__(self)
        self._vm_widget=vm_widget
        self._spice_session=vm_widget.spice_session
        usb_dm=SpiceClientGLib.UsbDeviceManager.get(self._spice_session)
        usb_dm.connect("device-added", self._device_added_cb)
        usb_dm.connect("device-removed", self._device_removed_cb)
        usb_dm.connect("device-error", self._device_error_cb)
        self._usb_dm:SpiceClientGLib.UsbDeviceManager=usb_dm

        user_service_socket_path=os.path.join("/bubble/run", "padsi-userv.sock")
        self._api_socket_path="http+unix://"+parse.quote_plus(user_service_socket_path)
        self._api_session=requests_unixsocket.Session()

        self._usb_redirection_classes:list[str]=["mass-storage", "smartcard"] # classes of USB devices which can be "redirected" to the VM
        self._devices:dict[SpiceClientGLib.UsbDevice,USBDevice|None]={} # devices presented to the user
        self._devices_analyzed=False # devices are analysed the 1st time they are required, not before as the
                                     # session may not yet be fully "connected" and redirection may not be possible
        self._devices_popover=None
        self._devices_vbox=None
        self._keyboard_popover=None
        self._reveal=Gtk.Revealer()
        self.attach(self._reveal, 0, 0, 1, 1)
        self._reveal.show()
        self.reveal()
        self.set_property("halign", Gtk.Align.CENTER)

        # enabled features
        self._has_keyboard=True

        bb=Gtk.HBox()
        self._reveal.add(bb)

        icons_path=os.path.join(os.path.dirname(os.path.realpath(__file__)), "icons")

        # fullscreen
        button=Gtk.ToggleButton()
        button.set_tooltip_text("Toggle fullscreen")
        image=_load_image_file(os.path.join(icons_path, "fullscreen.png"))
        button.set_image(image)
        bb.add(button)
        self._fullscreen_button=button
        self._fullscreen_button_sigid=button.connect("toggled", self._button_fullscreen_cb)
        self._vm_widget.connect("size-allocate", self._size_allocate_cb, button)

        # USB devices to connect
        button=Gtk.Button()
        button.set_tooltip_text("Transfer USB devices")
        image=_load_image_file(os.path.join(icons_path, "usb.png"))
        button.set_image(image)
        bb.add(button)
        button.connect("clicked", self._button_devices_clicked_cb)
        button.connect("show", self._dev_button_show_cb)
        self._dev_button=button
        self._dev_widgets_update_timer=None

        # send keyboard keys combinations
        button=Gtk.Button()
        button.set_tooltip_text("Send keystrokes")
        image=_load_image_file(os.path.join(icons_path, "keyboard.png"))
        button.set_image(image)
        bb.add(button)
        button.connect("clicked", self._button_keyboard_clicked_cb)
        button.connect("show", self._button_keyboard_show_cb)
        self._keyb_button=button

        # hide button
        button=Gtk.Button()
        button.set_tooltip_text("Hide this window")
        image=_load_image_file(os.path.join(icons_path, "eye.png"))
        button.set_image(image)
        bb.add(button)
        button.connect("clicked", self._button_hide_console_clicked_cb)
        self._hide_button=button

        # shutdown button
        button=Gtk.Button()
        button.set_tooltip_text("Shut down Virtual Machine")
        image=_load_image_file(os.path.join(icons_path, "shutdown.png"))
        button.set_image(image)
        bb.add(button)
        button.connect("clicked", self._button_shutdown_vm_clicked_cb)
        self._shutdown_button=button

        # discard button
        button=Gtk.Button()
        button.set_tooltip_text("Discard Virtual Machine")
        image=_load_image_file(os.path.join(icons_path, "trash.png"))
        button.set_image(image)
        bb.add(button)
        button.connect("clicked", self._button_discard_vm_clicked_cb)
        self._discard_button=button

        bb.show_all()

    def _size_allocate_cb(self, widget, rect, toggle_button):
        # ensure that the toggle button's position is always on par with the actual window state
        topwin=widget.get_ancestor(Gtk.Window)
        if topwin:
            gdkwin=topwin.get_window()
            if gdkwin:
                is_full=True if gdkwin.get_state() & Gdk.WindowState.FULLSCREEN else False
                GObject.signal_handler_block(self._fullscreen_button, self._fullscreen_button_sigid)
                toggle_button.set_active(is_full)
                GObject.signal_handler_unblock(self._fullscreen_button, self._fullscreen_button_sigid)

    def _reset_devices_visual_state(self):
        if self._devices_vbox is not None:
            for dwid in self._devices_vbox.get_children():
                if dwid.dev is None:
                    continue # dwid is not a USBDeviceWidget
                dwid.set_sensitive(dwid.get_active())

    def _update_devices_visual_state(self):
        devices=[dev.id for dev in self._devices.values() if dev is not None]
        if len(devices)==0:
            return

        params={"devices": ",".join(devices)}
        resp=self._api_session.get(f"{self._api_socket_path}/usb-device", params=params, timeout=1)
        if resp.ok:
            data=resp.json() # will be like: {"003/012": {"used-by": null, "reserved-by": null}}
            syslog.syslog(syslog.LOG_ERR, f"{data=}")
            if self._devices_vbox is not None:
                for dwid in self._devices_vbox.get_children():
                    try:
                        dev=dwid.dev
                        if dev is None:
                            continue # dwid is not a USBDeviceWidget
                        try:
                            can_use=data[dev.id].get("used-by") is None and data[dev.id].get("reserved-by") is None
                        except Exception:
                            can_use=False
                            syslog.syslog(syslog.LOG_ERR, f"CODEBUG: no information for device {dev.id} in reply from /usb-device API call")

                        # if active, then we must not set it to non sensitive otherwise the device can't be detached
                        if dwid.get_active():
                            dwid.set_sensitive(True)
                            dwid.mark_as_attached(None)
                        else:
                            dwid.set_sensitive(can_use)
                            if can_use:
                                dwid.mark_as_attached(None)
                            else:
                                user=data[dev.id].get("used-by")
                                if user is None:
                                    user=data[dev.id].get("reserved-by")
                                else:
                                    user=", ".join(user)
                                dwid.mark_as_attached(user)
                    except Exception as e:
                        syslog.syslog(syslog.LOG_ERR, f"CODEBUG in _update_devices_visual_state: {str(e)}")
        else:
            syslog.syslog(syslog.LOG_ERR, f"User service for GET /usb-device returned an error: {resp.text}")
        return True # keep the timer

    def _dev_button_show_cb(self, _=None):
        if len(self._usb_redirection_classes)==0:
            self._dev_button.hide()
            if self._devices_popover is not None:
                self._devices_popover.hide()
        else:
            self._dev_button.show()

    def _button_keyboard_show_cb(self, _=None):
        if self._has_keyboard:
            self._keyb_button.show()
        else:
            self._keyb_button.hide()

    @property
    def has_keyboard(self):
        return self._has_keyboard

    @has_keyboard.setter
    def has_keyboard(self, value):
        self._has_keyboard=value
        self._button_keyboard_show_cb()

    @property
    def usb_redirection_classes(self) -> list[str]:
        return self._usb_redirection_classes

    @usb_redirection_classes.setter
    def usb_redirection_classes(self, classes:list[str]):
        self._usb_redirection_classes=classes
        self._dev_button_show_cb()

    def _send_key_cb(self, button, codes):
        display=self._vm_widget.spice_display
        display.send_keys(codes, SpiceClientGtk.DisplayKeyEvent.PRESS)
        display.send_keys(codes, SpiceClientGtk.DisplayKeyEvent.RELEASE)
        if self._keyboard_popover is not None:
            self._keyboard_popover.hide()

    def _button_keyboard_clicked_cb(self, button):
        if self._keyboard_popover is None:
            popover=Gtk.Popover()
            popover.set_relative_to(self._keyb_button)

            grid=Gtk.Grid()
            grid.set_row_spacing(10)
            grid.set_column_spacing(10)
            grid.set_property("row-spacing", 0)

            keys={
                "Ctrl+Alt+Del": [Gdk.KEY_Control_L, Gdk.KEY_Alt_L, Gdk.KEY_Delete],
                "Ctrl+Alt+BackSpace": [Gdk.KEY_Control_L, Gdk.KEY_Alt_L, Gdk.KEY_BackSpace],
                "Ctrl+Alt+F1": [Gdk.KEY_Control_L, Gdk.KEY_Alt_L, Gdk.KEY_F1],
                "Ctrl+Alt+F2": [Gdk.KEY_Control_L, Gdk.KEY_Alt_L, Gdk.KEY_F2],
                "Ctrl+Alt+F3": [Gdk.KEY_Control_L, Gdk.KEY_Alt_L, Gdk.KEY_F3],
                "Ctrl+Alt+F4": [Gdk.KEY_Control_L, Gdk.KEY_Alt_L, Gdk.KEY_F4],
                "Ctrl+Alt+F5": [Gdk.KEY_Control_L, Gdk.KEY_Alt_L, Gdk.KEY_F5]
            }
            top=0
            for combo in keys:
                button=Gtk.Button(label=combo)
                button.connect("clicked", self._send_key_cb, keys[combo])
                button.set_property("relief", Gtk.ReliefStyle.NONE)
                grid.attach(button, 0, top, 1, 1)
                top+=1

            popover.add(grid)
            popover.show_all()
            self._keyboard_popover=popover
        else:
            self._keyboard_popover.show()

    #
    # devices handling
    #
    def _device_added_cb(self, usb_dm:SpiceClientGLib.UsbDeviceManager, usb_dev:SpiceClientGLib.UsbDevice):
        """Signalled by Spice's device manager: a device has been added"""
        syslog.syslog(syslog.LOG_INFO, "Device added: %s"%usb_dev)
        if self._devices_analyzed:
            dev=USBDevice.new_from_spice_device(self._usb_dm, self._usb_redirection_classes, usb_dev, with_plugged_timestamp=True)
            self._devices[usb_dev]=dev
            if dev is not None and self._devices_popover:
                self._add_device_entry(dev)
        else:
            # stash the device to be analysed later
            self._devices[usb_dev]=None

    def _device_removed_cb(self, usb_dm:SpiceClientGLib.UsbDeviceManager, usb_dev:SpiceClientGLib.UsbDevice):
        """Signalled by Spice's device manager: a device has been removed"""
        syslog.syslog(syslog.LOG_INFO, "Device removed: %s"%usb_dev)
        dev=self._devices[usb_dev]
        if dev is not None and self._devices_popover:
            self._remove_device_entry(dev)
        del self._devices[usb_dev]

    def _device_error_cb(self, usb_dm:SpiceClientGLib.UsbDeviceManager, usb_dev:SpiceClientGLib.UsbDevice, error):
        """Signalled by Spice's device manager: a device has issued an error"""
        syslog.syslog(syslog.LOG_ERR, "Device error: %s / %s"%(usb_dev, error))
        del self._devices[usb_dev]

    def _device_connect_result_cb(self, usb_dev:SpiceClientGLib.UsbDevice, res, dev:USBDevice):
        """Called when the operation of connecting a device terminates"""
        try:
            if self._usb_dm.connect_device_finish(res): # True if device is now connected (https://lazka.github.io/pgi-docs/SpiceClientGLib-2.0/mapping.html)
                dev.state=USBDeviceState.REDIRECTED
                syslog.syslog(syslog.LOG_INFO, "Device connected: %s"%dev.descr)
            else:
                raise Exception("no detail")
        except Exception as e:
            dev.state=USBDeviceState.NOT_REDIRECTED
            syslog.syslog(syslog.LOG_INFO, "Device not connected: %s / %s"%(dev.descr, e.args[0]))
            self.emit("error", "Could not redirect device to VM", e.args[0])

    def _device_disconnect_result_cb(self, usb_dev:SpiceClientGLib.UsbDevice, res, dev:USBDevice):
        """Called when the operation of disconnecting a device terminates"""
        try:
            if self._usb_dm.disconnect_device_finish(res): # True if device is now disconnected
                syslog.syslog(syslog.LOG_INFO, "Device no more connected: %s"%dev.descr)
                dev.state=USBDeviceState.NOT_REDIRECTED
            else:
                raise Exception("no detail")
        except Exception as e:
            syslog.syslog(syslog.LOG_ERR, "Device not disconnected: %s / %s"%(dev.descr, e.args[0]))
            self.emit("error", "Could not stop device redirection", e.args[0])

    def _button_devices_clicked_cb(self, button):
        """Called to show the devices which are currently redirected and the one which can be redirected
        """
        # force devices anlysis if not yet done
        if not self._devices_analyzed:
            for usb_dev in list(self._devices.keys()):
                self._devices[usb_dev]=USBDevice.new_from_spice_device(self._usb_dm, self._usb_redirection_classes, usb_dev, with_plugged_timestamp=False)
            self._devices_analyzed=True

        # build widgets if necessary
        if not self._devices_popover:
            self._devices_popover=Gtk.Popover()
            self._devices_popover.set_relative_to(self._dev_button)
            self._devices_popover.connect("show", self._dev_popover_show_cb)
            self._devices_popover.connect("hide", self._dev_popover_hide_cb)

            vbox=Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
            vbox.set_property("margin", 10)
            self._devices_vbox=vbox

            # label displayed when no device can be shared with the VM
            label=Gtk.Label(label="N/A")
            vbox.pack_start(label, False, False, 0)
            label.show()
            label.dev=None # trick used in _remove_device_entry() and other places
            label.connect("show", self._devices_none_label_show_cb)
            self._no_device_label=label

            # add already found devices
            for (_, dev) in self._devices.items():
                if dev:
                    self._add_device_entry(dev)

            self._devices_popover.add(vbox)
            self._devices_popover.show_all()
        else:
            self._devices_popover.show()

    def _dev_popover_show_cb(self, _):
        if self._dev_widgets_update_timer is None:
            self._reset_devices_visual_state()
            self._dev_widgets_update_timer=GLib.timeout_add_seconds(1, self._update_devices_visual_state)

    def _dev_popover_hide_cb(self, _):
        if self._dev_widgets_update_timer is not None:
            GLib.source_remove(self._dev_widgets_update_timer)
            self._dev_widgets_update_timer=None

    def _devices_none_label_show_cb(self, widget):
        if self._devices_vbox is not None:
            children=self._devices_vbox.get_children()
            nbchildren=len(children)
            if nbchildren!=1:
                self._no_device_label.hide()

    def _add_device_entry(self, dev:USBDevice):
        # Add widget associated to @dev
        if self._devices_vbox is not None:
            dwid=USBDeviceWidget(dev)
            dwid.connect("add-redirection", self._add_redirection_cb)
            dwid.connect("del-redirection", self._del_redirection_cb)

            self._devices_vbox.pack_start(dwid, False, False, 0)

            dwid.show()
            self._no_device_label.hide()

    def _remove_device_entry(self, dev:USBDevice):
        # Removing widgets associated to @dev
        if self._devices_vbox is not None:
            children=self._devices_vbox.get_children()
            nbchildren=len(children)-1
            for child in children:
                if child.dev==dev:
                    self._devices_vbox.remove(child)
            if nbchildren==1: # only the self._devices_none_label remains
                self._no_device_label.show()

    def _add_redirection_cb(self, dev_wid:USBDeviceWidget):
        dev:USBDevice=dev_wid.dev
        data={"device-id": dev.id}
        resp=self._api_session.post(f"{self._api_socket_path}/usb-device", data=json.dumps(data),
                                    headers={"Content-Type": "application/json"}, timeout=5)
        syslog.syslog(syslog.LOG_ERR, f"requesting device reservation, got {resp.ok=}")
        if resp.ok:
            data=resp.json()
            dev.state=USBDeviceState.REDIRECT_START
            self._usb_dm.connect_device_async(dev.usb_dev, None, self._device_connect_result_cb, dev)
        else:
            self.emit("error", "Could not reserve device", resp.text)

    def _del_redirection_cb(self, dev_wid:USBDeviceWidget):
        dev:USBDevice=dev_wid.dev
        dev.state=USBDeviceState.REDIRECT_STOP
        self._usb_dm.disconnect_device_async(dev.usb_dev, None, self._device_disconnect_result_cb, dev)

    #
    # misc. other features
    #
    def _button_fullscreen_cb(self, button):
        fullscreen=button.get_active()
        self._vm_widget.spice_display.set_property("grab-keyboard", fullscreen)
        self.emit("fullscreen", fullscreen)

    def _button_discard_vm_clicked_cb(self, button):
        self.emit("discard")

    def _button_shutdown_vm_clicked_cb(self, button):
        self.emit("shutdown")

    def _button_hide_console_clicked_cb(self, button):
        self.emit("console-hide")

    def reveal(self):
        self._reveal.set_reveal_child(True)

    def unreveal(self):
        self._reveal.set_reveal_child(False)

    def set_vm_usage_is_run(self, usage_is_run:bool):
        """Hide some buttons if vm_run is False (for install, update or customisation)
        """
        if usage_is_run:
            self._dev_button.show()
            self._shutdown_button.show()
        else:
            self._dev_button.hide()
            self._shutdown_button.hide()

    def set_vm_always_discard(self, always_discard:bool):
        """Hide some buttons if always_discard is True
        """
        if always_discard:
            self._shutdown_button.hide()
        else:
            self._shutdown_button.show()

    def set_sensitive(self, sensitive:bool):
        """Change the sensitiveness of all the action buttons"""
        super().set_sensitive(sensitive)
        self._fullscreen_button.set_sensitive(sensitive)
        self._dev_button.set_sensitive(sensitive)
        self._shutdown_button.set_sensitive(sensitive)
        self._discard_button.set_sensitive(sensitive)

class VMConsoleWidget(Gtk.Overlay):
    """Actual viewer"""
    def __init__(self, usb_redirection_classes:list[str]|None=None, port:int|None=None, password:str|None=None, unix_socket:str|None=None):
        super().__init__()

        # widgets
        self._session=SpiceClientGLib.Session(enable_usbredir=True)
        if port is not None:
            self._session.set_property("uri", f"spice://localhost?port={port}")
        elif unix_socket is not None:
            if not os.path.isabs(unix_socket):
                raise Exception("Unix socket must be provided as an absolute path")
            self._session.set_property("uri", f"spice+unix://{unix_socket}")
        if password:
            self._session.set_property("password", password)

        self.connect("destroy", self._destroy_self_cb)
        self._session.connect_after("channel-new", self._channel_new_cb)

        self._actions=VMActions(self)
        if usb_redirection_classes is not None:
            self._actions.usb_redirection_classes=usb_redirection_classes
        self.add_overlay(self._actions)
        self.set_overlay_pass_through(self._actions, True)
        self._actions.show()

        # misc.
        self._input_channel=None
        self._display:SpiceClientGtk.Display=None
        gdk_display=Gdk.Display.get_default()
        seat=gdk_display.get_default_seat()
        self._pointer=seat.get_pointer()
        self._gdkwin=None

    @property
    def actions(self) -> VMActions:
        return self._actions

    @property
    def input_channel(self) -> SpiceClientGLib.InputsChannel:
        return self._input_channel

    @property
    def spice_session(self) -> SpiceClientGLib.Session:
        return self._session

    @property
    def spice_display(self) -> SpiceClientGtk.Display:
        return self._display

    def _mouse_move_cb(self, window, event):
        if self._gdkwin is None:
            self._gdkwin=window.get_window()
        (dummy, x, y, mask)=self._gdkwin.get_device_position(self._pointer)
        r=False
        if y<10:
            win_w=self._gdkwin.get_width()
            mid=win_w/2
            act_w=self._actions.get_allocated_width()
            if x>=mid-act_w and x<=mid+act_w:
                self._actions.reveal()
                r=True
        if not r:
            self._actions.unreveal()
        return False

    def session_connect(self):
        # called during app start
        self._session.connect()

    def _destroy_self_cb(self, _):
        if self._session is not None:
            self._session.disconnect()

    def _channel_event(self, channel, event):
        if _debug:
            syslog.syslog(syslog.LOG_DEBUG, "Spice main channel event %s"%event)
        if event not in (SpiceClientGLib.ChannelEvent.OPENED, SpiceClientGLib.ChannelEvent.CLOSED):
            syslog.syslog(syslog.LOG_ERR, "VMWidget: connection to VM failed")

    def _channel_new_cb(self, session, channel):
        ctype=channel.get_property("channel-type")
        if _debug:
            syslog.syslog(syslog.LOG_DEBUG, "Spice new channel: %s, type: %s"%(channel, ctype))
        if ctype==1: # main channel
            channel.connect_after("channel-event", self._channel_event)
            #channel.set_property("mouse-mode", 1)
        elif ctype==2: # display channel
            cid=channel.get_property("channel-id")
            self._display=SpiceClientGtk.Display.new(self._session, cid)
            self._display.set_property("resize-guest", True)
            self._display.set_property("keypress-delay", 0)
            self._display.set_property("grab-keyboard", False) # avoid GNOME's annoying popup about inhibiting keyboard shortcuts
            self._display.set_property("scaling", True)
            self.add(self._display)
            self._display.show()
            self.set_focus_child(self._display)

            self._display.add_events(Gdk.EventMask.POINTER_MOTION_MASK)
            self._display.connect("motion-notify-event", self._mouse_move_cb)
        elif ctype==3: # input channel
            self._input_channel=channel

    @staticmethod
    def get_sane_default_size() -> tuple[int,int]:
        """Compute a default reasonable size for the widget's window"""
        display=Gdk.Display.get_default()
        w=1920
        h=1080
        for index in range(0, display.get_n_monitors()):
            mon=display.get_monitor(index)
            rect=mon.get_workarea()
            w=min(w, rect.width)
            h=min(h, rect.height)
        w=max(w-200, 1080)
        h=max(h-200, 824)
        if w/h>4/3:
            w=h*4/3
        return (int(w),h)
