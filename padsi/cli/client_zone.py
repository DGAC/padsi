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
# PADSI client API implementation when running from within a zone
#

from __future__ import annotations

from client import BaseClient, VMStatus


class Client(BaseClient):
    """Object to interact with a PADSI user service"""
    def __init__(self, uid:int|None=None):
        super().__init__("/bubble/run/padsi-zserv.sock")

    def hello(self):
        """Say Hello!"""
        return self.post("/hello", "from me")

    def vms(self):
        """Get the list of VMs which can be run in the zone"""
        return self.get("/vms")

    def vm_discard(self, vm_id:str, vm_version:str, force:bool=False):
        data={
            "action": "discard",
            "vm-id": vm_id,
            "vm-version": vm_version,
            "force": force
        }
        return self.delete("/vm", data)

    def get_vm_status(self, vm_id:str, verbose:bool=False):
        data={
            "vm-id": vm_id,
            "verbose": verbose
        }
        data=self.get("/vm", data)
        if data is None:
            raise Exception(f"CODEBUG: invalid /vm data {data}")
        return VMStatus.from_data(data)

    def vm_start(self, vm_id:str, nickname:str|None=None):
        data={
            "action": "run",
            "vm-id": vm_id,
            "nickname": nickname
        }
        return self.post("/vm", data)

    def vm_display(self, vm_id:str, nickname:str|None=None):
        data={
            "vm-id": vm_id,
            "nickname": nickname
        }
        return self.post("/vm-display", data)

    def vm_launcher_create(self, vm_id:str, nickname:str|None=None):
        data={
            "vm-id": vm_id,
            "nickname": nickname
        }
        return self.post("/vm-launcher", data)

    def vm_launcher_delete(self, vm_id:str, nickname:str|None=None):
        data={
            "vm-id": vm_id,
            "nickname": nickname
        }
        return self.delete("/vm-launcher", data)
