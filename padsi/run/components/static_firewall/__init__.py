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
# This is the PADSI static firewall component
#

from __future__ import annotations

import json
import os
import tempfile

import nsbubble
import padsi.config
from padsi import fwlib

from .. import Component


class StaticFirewallException(Exception):
    pass

class StaticFirewall(Component):
    """Configure a bubble's netfilter firewall"""

    def __init__(self, fw_rules:list[padsi.config.FWRule], log_denied_spec:fwlib.LogSpec|None=None, log_only:bool=False):
        """program to set some pre-defined firewall rules
        """
        self._fw_rules=fw_rules
        self._log_denied_spec=log_denied_spec
        self._log_only=log_only
        self._sandbox_dir_obj:tempfile.TemporaryDirectory|None=None # stores all files needed to run the programs in the nsbubble
        self._sandbox_dir_name:str|None=None
        self._pid:int|None=None # process's PID

    def get_mountpoints(self) -> set[nsbubble.MountPoint]:
        script_dir=os.path.dirname(__file__)

        # copy the resources to a tmp directory
        if self._sandbox_dir_name is None:
            self._sandbox_dir_obj=tempfile.TemporaryDirectory()
            self._sandbox_dir_name=self._sandbox_dir_obj.name

            # FW rules
            data=[rule.format_for_component() for rule in self._fw_rules]
            fw_rules_path=f"{self._sandbox_dir_name}/fw-rules.json"
            with open(fw_rules_path, "wt") as fd:
                json.dump(data, fd)
        else:
            data=[rule.format_for_component() for rule in self._fw_rules]
            fw_rules_path=f"{self._sandbox_dir_name}/fw-rules.json"

        return {
            nsbubble.MountPoint(fw_rules_path, "/etc/fw-rules.json", monitored=True),
            nsbubble.MountPoint(script_dir, "/padsi-fw-bin"),
        }

    @property
    def capabilities(self) -> list[str]:
        return ["net_admin"]

    def start(self, api:nsbubble.BubbleAPI):
        """Actually start the required processes in a bubble using the api object
        """
        if self._pid is None:
            env={
                "PYTHONPATH": "/usr/share/padsi"
            }
            if self._log_only:
                env["LOG_ONLY"] = "yes"
            if self._log_denied_spec is None:
                self._pid=api.start_process(["/padsi-fw-bin/padsi-static-fw"], extra_env=env, ignore_status=False, capabilities="net_admin")
            else:
                self._pid=api.start_process(["/padsi-fw-bin/padsi-static-fw", str(self._log_denied_spec)], extra_env=env, ignore_status=False, capabilities="net_admin")

    def stop(self, api:nsbubble.BubbleAPI):
        """Stop the process
        """
        if self._pid is not None:
            api.stop_process(self._pid)
            self._pid=None

        if self._sandbox_dir_obj is not None:
            self._sandbox_dir_obj.cleanup()
            self._sandbox_dir_obj=None
        self._sandbox_dir_name=None

    def serialize(self) -> dict:
        return {
            "class": self.__class__.__name__,
            "data": {
                "sandbox-dir": self._sandbox_dir_name,
                "pid": self._pid
            }
        }

    @classmethod
    def deserialize(cls, data:dict) -> StaticFirewall:
        ldata=data.get("data")
        if ldata is None:
            raise StaticFirewallException("CODEBUG: no 'data' found in deserialized data")
        obj=cls([])
        obj._sandbox_dir_name=ldata["sandbox-dir"]
        obj._pid=ldata["pid"]
        return obj
