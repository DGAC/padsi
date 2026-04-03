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

from client import BaseClient, VMStatus

import padsi.config
import padsi.run


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
class GlobalStatus:
    """Easy way to handle the status returned by the server"""
    uid: int
    available_zones:list[str]
    _active_zones:dict[str,list[ZoneStatus]]
    running_tasks:list[str]
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
        if uid is None or available_zones is None or running_tasks is None:
            raise Exception(f"CODEBUG: invalid global status data {data}")
        return cls(uid, available_zones, active_zones, running_tasks, data)

    @property
    def raw_data(self) -> dict:
        return self._raw_data

    @property
    def active_zones(self) -> list[str]:
        """Get the list of acives zones"""
        return list(self._active_zones.keys())

    def get_active_zones_status(self, zone_name:str) -> list[ZoneStatus]|None:
        return self._active_zones.get(zone_name)


class Client(BaseClient):
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

    def get_vm_status(self, vm_id:str, zone_name:str, verbose:bool=False) -> VMStatus:
        """Get a status of a VM"""
        data={
            "vm-id": vm_id,
            "zone": zone_name,
            "verbose": verbose
        }
        data=self.get("/vm", data)
        if data is None:
            raise Exception(f"CODEBUG: invalid /vm data {data}")
        return VMStatus.from_data(data)

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

    def vm_import(self, vm_id:str, hdd_file:str, vars_file:str, message:str|None):
        """Import a VM which was generated elsewhere
        """
        data={
            "action": "import",
            "vm-id": vm_id,
            "hdd-file": hdd_file,
            "vars-file": vars_file,
            "message": message
        }
        return self.post("/vm", data)

    def vm_save(self, conf:padsi.config.Configuration, vm_id:str, vm_name:str, ar_file:str, depend_at:str|None):
        """Save a VM version and (some) of its dependencies to a TAR archive which
        file name is specified via ar_file
        """
        # get the VM configuration
        vm_conf:padsi.config.VirtualMachine|None=None
        for vm in conf.get_vms_for_usage(padsi.config.VMUsage.RUN):
            if vm.id==vm_id:
                vm_conf=vm
                break
        if vm_conf is None:
            raise Exception(f"No VM with ID '{vm_id}'")

        # analyse passed VM name (nickname) and get the VM version
        (_userid, vtype, vnum, staged, _nickname)=padsi.run.parse_vm_version(vm_name)
        vmf=padsi.run.VMFiles(vm_conf.directory)
        if staged:
            raise Exception("Can't save staged VM version")
        else:
            if vtype==padsi.run.VMVersionType.BASE:
                if vnum is None:
                    raise Exception("Could not identify VM's version number")
                vm_version=vmf.get_base_version(vnum)
            else:
                raise Exception(f"Can't save {vtype.value} VM version")

        if vm_version is None:
            raise Exception("Could not find specified VM version")
        if not vm_version.is_complete:
            raise Exception("Specified VM version is not compelete (some files are missing)")
        if vm_version.state==padsi.run.VMState.RUNNING:
            raise Exception("Specified VM version is currently being used")

        depend_at_vmv=None
        if depend_at is not None:
            try:
                (_userid, vtype, vnum, staged, _nickname)=padsi.run.parse_vm_version(depend_at)
                if vtype==padsi.run.VMVersionType.BASE and vnum is not None:
                    depend_at_vmv=vmf.get_base_version(vnum)
            except Exception:
                pass
        padsi.run.VMArchive.create(vm_conf, vm_version, ar_file, depend_at_vmv)


    def vm_load(self, conf:padsi.config.Configuration, ar_file:str, vm_id:str|None, message:str|None):
        """Integrate a VM version from its files which have been uploaded to the staged/<load_id> directory
        """
        vm_ar=padsi.run.VMArchive(ar_file)
        # get the VM configuration
        if vm_id is not None:
            vm_ar.vm_id=vm_id

        vm_conf:padsi.config.VirtualMachine|None=None
        for vm in conf.get_vms_for_usage(padsi.config.VMUsage.RUN):
            if vm.id==vm_ar.vm_id:
                vm_conf=vm
                break
        if vm_conf is None:
            raise Exception(f"No VM with ID '{vm_ar.vm_id}'")

        # make sure the staging directory exists
        data={
            "vm-id": vm_ar.vm_id,
            "action": "create-staged-dir"
        }
        self.post("/vm", data)

        # extract the archive (the directory which contains the extracted files will be destroyed by the PADSI service)
        extract_id=vm_ar.extract(vm_conf)
        data={
            "action": "load",
            "vm-id": vm_ar.vm_id,
            "extract-id": extract_id,
            "message": message
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
        data={
            "action": "update",
            "vm-id": vm_id,
            "zone": zone_name,
            "extra-isos": extra_isos,
            "extra-iso-file": iso_file
        }
        return self.post("/vm", data)

    def vm_discard(self, vm_id:str, vm_version:str, force:bool=False) -> list[str]:
        """Discard a VM version (remove all the files associated to a VM version)
        Returns the VM versions which have been discarded
        Notes:
          - if staged is True:
            - version_type must be BASE
            - version_number must not be specified
            - the user must be allowed to install or update the specified VM
          - if staged is False:
            - if version_type is BASE, the user must be allowed to install or update the specified VM
            - the version_number must be specified
            - if the VM version is used by another VM version, then an exception is raised
        """
        data={
            "action": "discard",
            "vm-id": vm_id,
            "vm-version": vm_version,
            "force": force
        }
        return self.delete("/vm", data) # pyright: ignore

    def vm_clean(self, vm_id:str) -> list[str]:
        """Discard any VM version which is not used anymore
        Returns the VM versions which have been discarded
        """
        data={
            "action": "clean",
            "vm-id": vm_id
        }
        return self.delete("/vm", data) # pyright: ignore

    def vm_merge(self, vm_id:str, vm_version:str, message:str|None):
        """Merge the contents of a VM's QEMU image file with its originator QEMU image file
        """
        data={
            "action": "commit",
            "vm-id": vm_id,
            "vm-version": vm_version,
            "message": message
        }
        return self.put("/vm", data)

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
