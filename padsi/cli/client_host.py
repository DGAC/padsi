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


#
# PADSI client API implementation when running from the host ("init") namespace
#

from __future__ import annotations

import os
from dataclasses import dataclass

from client_admin import ClientAdmin

@dataclass
class ZoneStatus:
    """Represents the status of a zone"""
    name:str
    ready:bool
    vm_conf:str|None
    vm_snap:str|None
    vm_nickname:str|None

    @classmethod
    def from_data(cls, zone_name:str, data:dict) -> ZoneStatus:
        ready=data.get("ready", False)
        vm_conf=data.get("vm-conf")
        vm_snap=data.get("vm-snap")
        vm_nickname=data.get("vm-nickname")

        return cls(zone_name, ready, vm_conf, vm_snap, vm_nickname)

@dataclass
class TrafficShaper:
    name: str
    type: str
    functionnal: bool

@dataclass
class GlobalStatus:
    """Easy way to handle the status returned by the server"""
    uid: int
    available_zones:list[str]
    _active_zones:dict[str,list[ZoneStatus]]
    running_tasks:list[str]
    traffic_shapers:dict[str,TrafficShaper]
    _raw_data:dict

    @classmethod
    def from_data(cls, data:dict) -> GlobalStatus:
        uid=data.get("uid")
        available_zones=data.get("zones-available")
        active_zones:dict[str,list[ZoneStatus]]={}
        for (name, zdatalist) in data.get("active-zones", {}).items():
            for zdata in zdatalist:
                if name not in active_zones:
                    active_zones[name]=[]
                active_zones[name].append(ZoneStatus.from_data(name, zdata))
        running_tasks=data.get("running-tasks")
        tsp={}
        for (name, tdata) in data.get("network",{}).get("traffic-shapers",{}).items():
            tsp[name]=TrafficShaper(name, tdata.get("type"), tdata.get("functionnal"))
        if uid is None or available_zones is None or running_tasks is None:
            raise Exception(f"CODEBUG: invalid global status data {data}")
        return cls(uid, available_zones, active_zones, running_tasks, tsp, data)

    @property
    def raw_data(self) -> dict:
        return self._raw_data

    @property
    def active_zones(self) -> list[str]:
        """Get the list of acives zones"""
        return list(self._active_zones.keys())

    def get_active_zones_status(self, zone_name:str) -> list[ZoneStatus]|None:
        return self._active_zones.get(zone_name)


class Client(ClientAdmin):
    """Object to interact with a PADSI user service"""
    def __init__(self, uid:int|None=None):
        uid=os.getuid() if uid is None else uid
        super().__init__(f"/run/user/{uid}/padsi-userv.sock")

    def get_status(self) -> GlobalStatus:
        """Get a global status of the user service"""
        data=self.get("/status")
        if data is None:
            raise Exception(f"CODEBUG: invalid /status data {data}")
        return GlobalStatus.from_data(data)

    def run(self, zone_name:str, args:list[str]):
        """Run a program in a specific zone"""
        data={
            "zone": zone_name,
            "args": args
        }
        return self.post("/procs", data)

    def vm_install(self, vm_id:str, boot_iso:str|None=None, extra_isos:list[str]|None=None, iso_file:str|None=None, zone_name:str|None=None):
        """Install a virtual machine
        The extra_isos argument may contain some ISO files' paths which will be used AS-IS by the VM,
        and the iso_file may be a single ISO file which will be removed when possible.

        Note:
          - this uses the network settings or the specified zone, if any, or determines it automatically
            if the specified zone does not offer the correct networking capabilities, or if not zone
            offers the correct networking capabilities if no zone is specified, then return an error
          - the user must be allowed to install the specified VM
        """
        data={
            "action": "install",
            "vm-id": vm_id,
            "zone": zone_name,
            "boot-iso": boot_iso,
            "extra-isos": extra_isos,
            "extra-iso-file": iso_file
        }
        return self.post("/vm", data)

    def vm_update(self, vm_id:str, extra_isos:list[str]|None=None, iso_file:str|None=None, zone_name:str|None=None):
        """Update a virtual machine
        The extra_isos argument may contain some ISO files' paths which will be used AS-IS by the VM,
        and the iso_file may be a single ISO file which will be removed when possible.

        Note:
          - this uses the network settings or the specified zone, if any, or determines it automatically
            if the specified zone does not offer the correct networking capabilities, or if not zone
            offers the correct networking capabilities if no zone is specified, then return an error
          - the user must be allowed to update the specified VM
        """
        # we need to pass full path for all the extra ISOs
        ex_isos:list[str]|None=None
        if extra_isos is not None:
            ex_isos=[os.path.realpath(fname) for fname in extra_isos]

        data={
            "action": "update",
            "vm-id": vm_id,
            "zone": zone_name,
            "extra-isos": ex_isos,
            "extra-iso-file": iso_file
        }
        return self.post("/vm", data)

    def vm_publish(self, vm_id:str, message:str|None):
        """Publish a staged a virtual machine image
        """
        data={
            "action": "publish",
            "vm-id": vm_id,
            "message": message
        }
        return self.put("/vm", data)

    def vm_start(self, vm_id:str, zone_name:str, nickname:str|None=None):
        """Start a virtual machine in a specific zone
        The name argument allows to give a nickname to the VM
        If the VM is already running, it is shown
        """
        data={
            "action": "run",
            "vm-id": vm_id,
            "zone": zone_name,
            "nickname": nickname
        }
        return self.post("/vm", data)

    def vm_display(self, vm_id:str, zone_name:str, nickname:str|None=None):
        """Show the VM viewer of a running virtual machine
        """
        data={
            "vm-id": vm_id,
            "zone": zone_name,
            "nickname": nickname
        }
        return self.post("/vm-display", data)

    def vm_launcher_create(self, vm_id:str, zone_name:str, nickname:str|None=None):
        """Create a desktop entry to launch/show a VM
        """
        data={
            "vm-id": vm_id,
            "zone": zone_name,
            "nickname": nickname
        }
        return self.post("/vm-launcher", data)

    def vm_launcher_delete(self, vm_id:str, zone_name:str, nickname:str|None=None):
        """Delete a desktop entry previously created
        """
        data={
            "vm-id": vm_id,
            "zone": zone_name,
            "nickname": nickname
        }
        return self.delete("/vm-launcher", data)
