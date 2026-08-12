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


#
# PADSI client API implementation when running from the host ("init") namespace
# using the admin-service
#

from __future__ import annotations

import padsi.config
import padsi.run

from client import BaseClient, VMStatus


class ClientAdmin(BaseClient):
    """Object to interact with a PADSI admin service"""
    def __init__(self, socket:str|None=None):
        if socket is not None:
            super().__init__(socket)
        else:
            super().__init__("/run/padsi/padsi/padsi-sserv.sock")

    def get_vm_status(self, vm_id:str, zone_name:str|None, verbose:bool=False) -> VMStatus:
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
        self.post("/vm", data)

        data={
            "action": "publish",
            "vm-id": vm_id,
            "message": message
        }
        return self.put("/vm", data)

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
            (_userid, vtype, vnum, staged, _nickname)=padsi.run.parse_vm_version(depend_at)
            if vtype==padsi.run.VMVersionType.BASE and vnum is not None:
                depend_at_vmv=vmf.get_base_version(vnum)
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

        # extract the archive (the directory which contains the extracted files will be destroyed by the PADSI service)
        extract_id=vm_ar.extract(vm_conf)
        data={
            "action": "load",
            "vm-id": vm_ar.vm_id,
            "extract-id": extract_id,
            "message": message
        }
        return self.post("/vm", data)

    def vm_discard(self, vm_id:str, vm_version:str, zone_name:str|None, force:bool=False) -> list[str]:
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
            "zone": zone_name,
            "vm-version": vm_version,
            "force": force
        }
        return self.delete("/vm", data) # pyright: ignore

    def vm_clean(self, vm_id:str, zone_name:str|None) -> list[str]:
        """Discard any VM version which is not used anymore
        Returns the VM versions which have been discarded
        """
        data={
            "action": "clean",
            "vm-id": vm_id,
            "zone": zone_name
        }
        return self.delete("/vm", data) # pyright: ignore

    def vm_merge(self, vm_id:str, vm_version:str, zone_name:str, message:str|None):
        """Merge the contents of a VM's QEMU image file with its originator QEMU image file
        """
        data={
            "action": "merge",
            "vm-id": vm_id,
            "vm-version": vm_version,
            "zone": zone_name,
            "message": message
        }
        return self.put("/vm", data)
