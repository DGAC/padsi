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
# This is the PADSI FUSE component
#

from __future__ import annotations

import os
import signal
import subprocess
import syslog

import nsbubble

from .. import Component

_debug=False

class Fuse(Component):
    """Fuse component to proxy the usage of "fusermount" and "umount" via a dedicated "mount-server.py" process
    """
    def __init__(self, socket_path:str, logs_dir:str):
        """Create component
        """
        self._dirs_list:list[str]=[]
        self._socket_path=socket_path
        self._logs_dir=logs_dir
        self._proc:subprocess.Popen|None=None
        self._pid:int|None=None # PID of the mount-server.py

    def declare_bubble_mounted_dir(self, bubble_dir:str, host_dir:str):
        """Declare a directory in the bubble to be a directory mounted in the host, and
        in which sub directories can be mounted using FUSE.
        """
        self._dirs_list.append(bubble_dir)
        self._dirs_list.append(host_dir)

    def get_mountpoints(self) -> dict:
        """Get the mount points required by the component
        Cf. nsbubble's documentation for the formalism
        """
        script_dir=os.path.dirname(__file__)

        return {
            os.path.join(script_dir, "fusermount-proxy.py"): {
                "mount-point": "/usr/bin/fusermount",
                "read-only": True,
                "monitored": False
            },
            os.path.join(script_dir, "umount-proxy.py"): {
                "mount-point": "/usr/bin/umount",
                "read-only": True,
                "monitored": False
            }
        }

    def start(self, api:nsbubble.BubbleAPI):
        if self._pid is None:
            script_dir=os.path.dirname(__file__)
            args=[os.path.join(script_dir, "mount-server.py"), self._socket_path]+self._dirs_list
            if _debug:
                syslog.syslog(syslog.LOG_DEBUG, f"Running mount-server: {args=}")
            self._proc=subprocess.Popen(args)
            self._pid=self._proc.pid

    def stop(self, api:nsbubble.BubbleAPI):
        if self._pid is not None:
            try:
                os.kill(self._pid, signal.SIGKILL)
            except Exception:
                pass # process may already have been killed
            if self._proc is not None:
                self._proc.wait()
                self._proc=None
            self._pid=None

    def serialize(self) -> dict:
        return {
            "class": self.__class__.__name__,
            "data": {
                "logs-dir": self._logs_dir,
                "pid": self._pid
            }
        }

    @classmethod
    def deserialize(cls, data:dict) -> Fuse:
        ldata=data.get("data")
        if ldata is None:
            raise Exception("CODEBUG: no 'data' found in deserialized data")
        obj=cls("dummy", logs_dir=ldata["logs-dir"])
        obj._pid=ldata["pid"]
        return obj
