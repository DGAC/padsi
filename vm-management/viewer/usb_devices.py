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

import datetime
import enum
import json
import os
import re
import subprocess
import syslog

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GObject, Gtk  # noqa: E402 # pyright: ignore

gi.require_version('SpiceClientGLib', '2.0')
from gi.repository import SpiceClientGLib  # noqa: E402 # pyright: ignore

_debug=False

class USBDeviceType(str, enum.Enum):
    """USB device types"""
    AUDIO = "audio"
    MASS_STORAGE = "mass-storage"
    SMARTCARD = "smartcard"
    NETWORK = "network"
    VIDEO = "video"
    OTHER = "other"

class USBDeviceState(str, enum.Enum):
    """USB device states with regards to the VM"""
    NOT_REDIRECTED = "NOT-REDIRECTED"
    REDIRECT_START = "REDIRECT-START" # transient state: redirected has been initiated
    REDIRECTED = "REDIRECTED"
    REDIRECT_STOP = "REDIRECT-STOP"   # transient state: stopping redirected

# refer to https://www.usb.org/defined-class-codes
_mapping={
    "1": USBDeviceType.AUDIO,
    "8": USBDeviceType.MASS_STORAGE,
    "10": USBDeviceType.NETWORK,
    "11": USBDeviceType.SMARTCARD,
    "14": USBDeviceType.VIDEO
}

def _get_usb_device_type(bus:int, addr:int) -> tuple[USBDeviceType,str,str]:
    """Identifies an USB device from its bus and address and returns (USBDeviceType, ID (<bus>/<dev>), human description)
    Note: we can't use vendor an product IDS because the same device may be plugged more than once
    """
    manufacturer:str|None=None
    product:str|None=None
    dtype:USBDeviceType=USBDeviceType.OTHER
    p=subprocess.run(["lsusb", "-v", "-s", f"{bus:03d}:{addr:03d}"], capture_output=True, text=True)
    if p.returncode!=0:
        raise Exception(f"Could not get infos. about USB device {bus:03d}:{addr:03d}")
    for line in p.stdout.splitlines():
        line=line.strip()
        if line.startswith("bInterfaceClass"):
            parts=line.split()
            if parts[1] in _mapping:
                dtype=_mapping[parts[1]]
        elif line.startswith("iManufacturer"):
            (*_, manufacturer)=line.split(maxsplit=2)
        elif line.startswith("iProduct"):
            (*_, product)=line.split(maxsplit=2)

    if manufacturer:
        human=f"{manufacturer} {product}" if product else manufacturer
    else:
        human=product if product else f"{bus:03d}:{addr:03d}"
    return (dtype, f"{bus:03d}/{addr:03d}", human)

def _get_root_live_partition(exception_if_no_live=True):
    """Get the live partition from which the system has booted.
    Returns devfile, for ex.: /dev/vda3"""
    # get the overlay's 'lower dir'
    p=subprocess.run(["mount"], capture_output=True, text=True)
    if p.returncode!=0:
        raise Exception(f"Could not list mount points: {p.stderr if p.stderr else p.stdout}")
    mounts=p.stdout
    ovline=None
    for line in mounts.splitlines():
        if line.startswith("overlay on / type overlay "):
            #  line will be like: overlay on / type overlay (rw,noatime,lowerdir=/run/live/rootfs/filesystem.squashfs/,upperdir=/run/live/overlay/rw,workdir=/run/live/overlay/work)
            ovline=line
            break
    if ovline is None:
        if exception_if_no_live:
            raise Exception("Could not identify the overlay filesystem")
        return None

    parts=re.split(r'\(|\)', ovline)
    params=re.split(r',', parts[1])
    lowerdir=None
    for param in params:
        if param.startswith("lowerdir="):
            (_, lowerdir)=param.split("=")
            # dir will be something like "/run/live/rootfs/filesystem.squashfs/"
            break
    if lowerdir is None:
        raise Exception("Could not identify overlay's lower dir")

    # get the loop device associated with the overlay's lower dir
    loopdev=None
    if lowerdir[-1]=="/":
        lowerdir=lowerdir[:-1]
    for line in mounts.splitlines():
        if f"on {lowerdir} type squashfs" in line:
            (loopdev, _)=line.split(" ", 1)
            break
    if loopdev!="/dev/loop0": # at this point, should always be loop0, otherwise something is very wrong...
        raise Exception(f"Unexpected loop device '{loopdev}'")

    # get the file serving as backend for the loopdev
    p=subprocess.run(["/sbin/losetup", "-l", "-J", loopdev], capture_output=True, text=True) # as JSON!
    if p.returncode!=0:
        raise Exception(f"Could not list loop devices set up: {p.stderr if p.stderr else p.stdout}")
    data=json.loads(p.stdout)
    backend=data["loopdevices"][0]["back-file"]

    # get the mounted device partition holding that backend file
    p=subprocess.run(["df", os.path.dirname(backend)], capture_output=True, text=True) # use the dirname and not the file itself for access permissions issues
    if p.returncode!=0:
        raise Exception(f"Could not use df: {p.stderr if p.stderr else p.stdout}")
    first=True
    for line in p.stdout.splitlines():
        if first:
            first=False
        else:
            (devfile, dummy)=line.split(" ", 1)
            # devfile will be like "/dev/vda3"
            if not devfile.startswith("/dev/vd") and not devfile.startswith("/dev/sd"):
                raise Exception(f"Invalid boot partition '{devfile}'")
            return devfile
    raise Exception(f"Internal error: boot partition is not mounted, where is the '{backend}' file ???")

class USBDevice(GObject.GObject):
    """Represents an USB device which can be "redirected" to a VM
    """
    # notation:
    # 'usb_dev' usually represents a SpiceUsbDevice
    # 'dev' usually represents a UsbDevice object (this class)
    __gsignals__ = {
        "state-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, usb_dev:SpiceClientGLib.UsbDevice, dev_id:str, descr:str, with_plugged_timestamp:bool):
        GObject.GObject.__init__(self)
        self._plugged_ts=datetime.datetime.now() if with_plugged_timestamp else None
        self._id=dev_id
        self._usb_dev=usb_dev
        self._descr=descr
        self._state=USBDeviceState.NOT_REDIRECTED

    @property
    def descr(self):
        if self._plugged_ts is None:
            return self._descr
        return f"{self._descr} (🔌 {self._plugged_ts.strftime('%H:%M:%S')})"

    @property
    def id(self) -> str:
        """ID (as <bus>/<device>) of the device, e.g. "003/012"
        """
        return self._id

    @property
    def usb_dev(self) -> SpiceClientGLib.UsbDevice:
        """Spice's UsbDevice object associated to the current object"""
        return self._usb_dev

    @property
    def state(self):
        return self._state

    @state.setter
    def state(self, state):
        state=USBDeviceState(state)
        if self._state!=state:
            self._state=state
            self.emit("state-changed")

    @classmethod
    def new_from_spice_device(cls, usb_dm: SpiceClientGLib.UsbDeviceManager, classes:list[str], usb_dev:SpiceClientGLib.UsbDevice, with_plugged_timestamp) -> USBDevice|None:
        """Analyse a specific USB device, and determine if it can be "connected"
        to the VM"""
        root_dev=_get_root_live_partition(exception_if_no_live=False)
        descr=usb_dev.get_description("%s|%s|%s|%d|%d") # manufacturer | product | [vendor_id:product_id] | bus(int) | address (int)
        add_to_redirect=False

        try:
            (manufacturer, product, vendorproduct, bus, addr)=descr.split("|")
            bus=int(bus)
            addr=int(addr)
            (vendor_id, product_id)=vendorproduct.split(":")
            (dtype, dev_id, human)=_get_usb_device_type(bus, addr)
            if usb_dm.can_redirect_device(usb_dev):
                if _debug:
                    syslog.syslog(syslog.LOG_DEBUG, f"Plugged device {dtype=}, {human=} can be redirected")
                add_to_redirect=True
                if "all" in classes:
                    pass
                elif dtype.value not in classes:
                    add_to_redirect=False

                #  in case we are using a live Linux, remove mass storage device if it's where the live Linux is
                if add_to_redirect and dtype==USBDeviceType.MASS_STORAGE and root_dev:
                    # check that this device is not the one where a live Linux resides
                    p=subprocess.run(["udevadm", "info", "-n", root_dev ,"-a"], capture_output=True, text=True)
                    if p.returncode==0:
                        found=0
                        for line in p.stdout.splitlines():
                            if '=="%s"'%vendor_id in line and "{idVendor}" in line:
                                found+=1
                            elif '=="%s"'%product_id in line and "{idProduct}" in line:
                                found+=1
                            if found==2:
                                add_to_redirect=False
                                break
            return cls(usb_dev, dev_id, human, with_plugged_timestamp) if add_to_redirect else None
        except Exception:
            return None

class USBDeviceWidget(Gtk.Box):
    """Widget to represent a USBDevice and allow the user to ask for its redirection
    """
    __gsignals__ = {
        "add-redirection": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "del-redirection": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, dev:USBDevice):
        self._dev=dev
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)

        # Create a label
        label=Gtk.Label(label=dev.descr)
        label.set_property("xalign", 0)
        self.pack_start(label, False, False, 0)

        # Create a switch
        switch=Gtk.Switch()
        if dev.state in (USBDeviceState.REDIRECT_START, USBDeviceState.REDIRECTED):
            switch.set_active(True)
        if dev.state in (USBDeviceState.REDIRECT_START, USBDeviceState.REDIRECT_STOP):
            switch.set_sensitive(False)

        switch.connect("state-set", self._switch_notif)
        dev.connect("state-changed", self._dev_state_changed_cb)

        self.pack_end(switch, False, False, 0)
        switch.set_valign(Gtk.Align.CENTER)
        self._switch=switch

        # Show children
        label.show()
        self._label=label
        self._switch.show()

    def get_active(self) -> bool:
        return self._switch.get_active()

    def mark_as_attached(self, user:str|None):
        """Modify the visual of the widget to indicate that
        the associated device is used by another user. The user argument can be:
        - "-" if the device is attached to a VM for the current user
        - not None if the device is attached to a VM by another user
        - None if the device is not attached
        """
        if user=="-":
            self._label.set_markup(f"{self._dev.descr}\n<small>already attached to another VM</small>")
        elif user is not None:
            self._label.set_markup(f"{self._dev.descr}\n<small>already attached by {user}</small>")
        else:
            self._label.set_label(self._dev.descr)

    def _dev_state_changed_cb(self, dev:USBDevice):
        sensitive=dev.state not in (USBDeviceState.REDIRECT_START, USBDeviceState.REDIRECT_STOP)
        self._switch.set_sensitive(sensitive)

        active=dev.state in (USBDeviceState.REDIRECTED, USBDeviceState.REDIRECT_START)
        active=dev.state==USBDeviceState.REDIRECTED
        self._switch.set_state(active)

    def _switch_notif(self, switch, state):
        if state:
            self.emit("add-redirection")
        else:
            self.emit("del-redirection")

    @property
    def dev(self) -> USBDevice:
        return self._dev
