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
import subprocess
import syslog
from dataclasses import dataclass

import padsi.misc

_debug=False

@dataclass
class MountPoint:
    """Represent a mount point"""
    mountpoint: str
    source_path: str
    readonly: bool

    def is_mounted(self) -> bool:
        """Tell if the mount point is mounted and, if mounted, verifies that the correct source path is mounted
        Note: does not check the read-only status
        """ 
        proc=subprocess.run(["findmnt", "-n", "-o", "SOURCE", self.mountpoint], capture_output=True, text=True)
        if proc.returncode!=0:
            if not proc.stderr:
                return False
            raise Exception(f"Could not run findmnt: {proc.stderr}")
        return True

    def mount(self) -> bool:
        """Mount a MountPoint
        Returns True if the mount point was not already mounted
        """
        if self.is_mounted():
            return False

        if _debug:
            syslog.syslog(syslog.LOG_DEBUG, f"Mounting {self.source_path} on {self.mountpoint}")
        if not os.path.exists(self.mountpoint):
            padsi.misc.makedirs_keep_owner(self.mountpoint)

        opt="bind,ro" if self.readonly else "bind"
        proc=subprocess.run(["mount", "-o", opt, self.source_path, self.mountpoint], capture_output=True, text=True)
        if proc.returncode!=0:
            raise Exception(f"Could not mount '{self.source_path}' on '{self.mountpoint}': {proc.stderr}")
        return True

    def umount(self) -> bool:
        """Unmount a mount point
        Returns True if the mount point was effectively mounted
        """
        if not self.is_mounted():
            return False
        if _debug:
            syslog.syslog(syslog.LOG_DEBUG, f"Unmounting {self.mountpoint} from {self.source_path}")
        proc=subprocess.run(["umount", self.mountpoint], capture_output=True, text=True)
        if proc.returncode!=0:
            raise Exception(f"Could not umount '{self.mountpoint}' from '{self.source_path}': {proc.stderr}")
        return True


    @staticmethod
    def load_from_data(mounts_data:dict, allow_absolute_destination_path:bool) -> list[MountPoint]|None:
        """Load a configuration block about mount points
        """
        if mounts_data is None:
            return None

        mounts=[]
        if not isinstance(mounts_data, dict):
            raise Exception("Invalid 'mounts' section")
        for mp, mdata in mounts_data.items():
            if not mp or not isinstance(mp, str):
                raise Exception(f"Invalid mount point '{mp}' in 'mounts' section")

            if os.path.isabs(mp) and not allow_absolute_destination_path:
                raise Exception(f"Invalid mount point: destination path '{mp}' must be relative and not absolute")

            mode=mdata.get("mode", "rw")
            if not mode or not isinstance(mode, str):
                raise Exception(f"Invalid mount point: invalid mode '{mode}'")
            source=mdata.get("source")
            if not source or not isinstance(source, str):
                raise Exception(f"Invalid source '{source}' in 'mounts' section")
            if os.path.isabs(source):
                raise Exception(f"Invalid mount point: source path {source} must be relative and not absolute")

            mounts.append(MountPoint(mp, source, mode!="rw"))

        return mounts
