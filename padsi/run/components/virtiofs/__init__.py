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

from __future__ import annotations

import os

import nsbubble
from padsi.config import MountPoint

from .. import Component


def _get_virtiofsd_binary_path() -> str:
    if os.path.exists("/usr/libexec/virtiofsd"):
        return "/usr/libexec/virtiofsd" # Debian >= 13 ('virtiofsd' package)
    raise Exception("Could not find the virtiofsd binary")

class VirtioFSServer(Component):
    """Virtiofs daemon"""

    def __init__(self, mountpoint:MountPoint, reference_dir:str|None=None):
        """Create a virtiofs server component.
        If the mountpoint's source path is absolute, then the reference_dir is not used, otherwise,
        the actual shared dir is <reference_dir>/<mountpoint.source_path>
        """
        self._mountpoint=mountpoint
        self._pid:int|None=None
        if os.path.isabs(self._mountpoint.source_path):
            self._shared_dir_in_host=self._mountpoint.source_path
        else:
            if not isinstance(reference_dir, str) or not os.path.isabs(reference_dir):
                raise Exception(f"Invalid reference_dir argument '{reference_dir}'")
            self._shared_dir_in_host=os.path.join(reference_dir, self._mountpoint.source_path)
        if not os.path.exists(self._shared_dir_in_host):
            os.makedirs(self._shared_dir_in_host)
        self._fsname=self._mountpoint.mount_path.replace("/", "_")
        self._shared_dir_in_bubble=f"/shared-{self._fsname}"

    @property
    def fsname(self) -> str:
        return self._fsname

    @property
    def socket_path(self) -> str:
        return os.path.join("/tmp", f"virtiofsd-{self.fsname}.socket")

    def get_mountpoints(self) -> dict:
        return {
            _get_virtiofsd_binary_path(): {
                "mount-point": "/tmp/virtiofsd",
                "read-only": True,
                "monitored": False
            },
            self._shared_dir_in_host: {
                "mount-point": self._shared_dir_in_bubble,
                "read-only": False,
                "monitored": False
            }
        }

    def start(self, api:nsbubble.BubbleAPI):
        """Actually start the required processes in a bubble using the api object
        """
        if self._pid is None:
            args=["/tmp/virtiofsd", "--shared-dir", self._shared_dir_in_bubble, "--socket-path", self.socket_path, "--sandbox", "none", "--syslog"]
            if self._mountpoint.read_only:
                args.append("--readonly")
            self._pid=api.start_process(args, ignore_status=False, restart=True)

    def stop(self, api:nsbubble.BubbleAPI):
        """Stop processes"""
        if self._pid is not None:
            api.stop_process(self._pid)
            self._pid=None

        if os.path.exists(self.socket_path):
            os.remove(self.socket_path)

    def serialize(self) -> dict:
        return {
            "class": self.__class__.__name__,
            "data": {
                "pid": self._pid,
                "mountpoint": self._mountpoint.mount_path,
                "source-path": self._mountpoint.source_path,
                "read-only": self._mountpoint.read_only
            }
        }

    @classmethod
    def deserialize(cls, data:dict) -> VirtioFSServer:
        ldata=data.get("data")
        if ldata is None:
            raise Exception("CODEBUG: no 'data' found in deserialized data")
        mp=MountPoint(ldata["source-path"], ldata["mountpoint"], ldata["read-only"])
        obj=cls(mountpoint=mp)
        obj._pid=ldata["pid"]
        return obj
