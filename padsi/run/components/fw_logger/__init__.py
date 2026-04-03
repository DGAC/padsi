#!/usr/bin/python3

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
# This is the PADSI Web proxy and/or Web redirection component
#

from __future__ import annotations

import os
import syslog

import nsbubble

from .. import Component

_debug=False

class FWLogger(Component):
    """Web server which can act as a Web proxy (and directly connect to the requested Web server or forward requests to some others Web proxies), and
    a Web "catch all" server which is able to reply as any web server which is not allowed in a specified zone:
    - creates a CA to generate any certificate
    - generates certificates on the fly
    - present a "blocked site" notice to the user in place of the requested site
    - if the user wants it, proposes to open the requested site in the same browser in another zone
    """
    def __init__(self, log_group:int):
        self._log_group=log_group
        self._pid:int|None=None # PID of the FW logger

    @property
    def capabilities(self) -> list[str]:
        return ["net_admin"]

    def start(self, api:nsbubble.BubbleAPI):
        if self._pid is None:
            script_dir=os.path.dirname(__file__)
            args=[os.path.join(script_dir, "fw-logger")]
            if _debug:
                syslog.syslog(syslog.LOG_DEBUG, "Starting FW logger component")
            env:dict[str,str]= {
                "LOG_GROUP": str(self._log_group),
                "LOG_DIR": "/var/log"
            }
            self._pid=api.start_process(args, ignore_status=False, extra_env=env, capabilities="net_admin", restart=True,
                child_stdout_file="/tmp/fw-logger.stdout", child_stderr_file="/tmp/fw-logger.stderr")
            if _debug:
                syslog.syslog(syslog.LOG_DEBUG, f"Started FW Logger component, PID: {self._pid}")

    def stop(self, api:nsbubble.BubbleAPI):
        if self._pid is not None:
            api.stop_process(self._pid)
            self._pid=None

    def serialize(self) -> dict:
        return {
            "class": self.__class__.__name__,
            "data": {
                "group": self._log_group,
                "pid": self._pid
            }
        }

    @classmethod
    def deserialize(cls, data:dict) -> FWLogger:
        ldata = data.get("data")
        if ldata is None:
            raise Exception("CODEBUG: no 'data' found in deserialized data")
        obj=cls(ldata["group"])
        obj._pid=ldata["pid"]
        return obj
