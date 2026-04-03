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

_debug=True

class WaylandProxy(Component):
    """
    """
    def __init__(self, socket_dir:str, zone_name:str, allowed_zones:set[str]):
        self._socket_dir=socket_dir
        self._zone_name=zone_name
        self._allowed_zones=allowed_zones
        self._pid:int|None=None # PID of the FW logger

        syslog.syslog(syslog.LOG_INFO, f"Wayland proxy: paste into {zone_name} is allowed from: {','.join(allowed_zones)}")

    def get_mountpoints(self) -> dict|None:
        denv:nsbubble.DisplayEnvironment=nsbubble.get_display_env()
        if denv.runtime_dir and denv.wayland_display:
            return {
                os.path.join(denv.runtime_dir, denv.wayland_display):{
                    "mount-point": "/bubble/run/wayland-0",
                    "read-only": False,
                    "monitored": False
                },
                self._socket_dir : {
                    "mount-point": "/bubble/run/wl-proxy",
                    "read-only": False,
                    "monitored": False
                }
            }
        syslog.syslog(syslog.LOG_WARNING, "Could not identify the Wayland environment in the host OS")
        return None

    @property
    def wayland_proxy_socket(self) -> str:
        """Name of the Wayland proxy's socket in the init namespace
        """
        return os.path.join(self._socket_dir, "wayland-proxy.sock")

    def start(self, api:nsbubble.BubbleAPI):
        if self._pid is None:
            script_dir=os.path.dirname(__file__)
            args=[
                os.path.join(script_dir, "wayland-proxy"),
                "/bubble/run/wayland-0", # real compositor's socket
                os.path.join("/bubble/run/wl-proxy", "wayland-proxy.sock"), # proxy's socket
                self._zone_name, # this zone's name
                ",".join(self._allowed_zones), # zones from which copy is allowed
                "block"
            ]
            if _debug:
                syslog.syslog(syslog.LOG_DEBUG, f"Starting Wayland Proxy component: {' '.join(args)}")
            env={"WAYLAND_DISPLAY": "wayland-0"}
            self._pid=api.start_process(args, ignore_status=False, extra_env=env, restart=True,
                child_stdout_file="/tmp/wayland-proxy.stdout", child_stderr_file="/tmp/wayland-proxy.stderr")
            if _debug:
                syslog.syslog(syslog.LOG_DEBUG, f"Started Wayland Proxy component, PID: {self._pid}")

    def stop(self, api:nsbubble.BubbleAPI):
        if self._pid is not None:
            api.stop_process(self._pid)
            self._pid=None

    def serialize(self) -> dict:
        return {
            "class": self.__class__.__name__,
            "data": {
                "socket-dir": self._socket_dir,
                "pid": self._pid
            }
        }

    @classmethod
    def deserialize(cls, data:dict) -> WaylandProxy:
        ldata = data.get("data")
        if ldata is None:
            raise Exception("CODEBUG: no 'data' found in deserialized data")
        obj=cls(ldata["socket-dir"], "dummy", set())
        obj._pid=ldata["pid"]
        return obj
