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

from nsbubble import MountPoint as BubbleMountPoint


class MountPointConfException(Exception):
    pass

class MountPoint(BubbleMountPoint):
    """Represent a mount point, a simple wrapper around nsbubble's MountPoint"""
    @staticmethod
    def load_from_data(mounts_data:dict, allow_absolute_destination_path:bool) -> set[MountPoint]|None:
        """Load a configuration block about mount points
        """
        if mounts_data is None:
            return None

        mounts=set()
        if not isinstance(mounts_data, dict):
            raise MountPointConfException("Invalid 'mounts' section")
        for mp, mdata in mounts_data.items():
            if not mp or not isinstance(mp, str):
                raise MountPointConfException(f"Invalid mount point '{mp}' in 'mounts' section")

            if os.path.isabs(mp) and not allow_absolute_destination_path:
                raise MountPointConfException(f"Invalid mount point: destination path '{mp}' must be relative and not absolute")

            mode=mdata.get("mode", "rw")
            if not mode or not isinstance(mode, str):
                raise MountPointConfException(f"Invalid mount point: invalid mode '{mode}'")
            source=mdata.get("source")
            if not source or not isinstance(source, str):
                raise MountPointConfException(f"Invalid source '{source}' in 'mounts' section")
            if os.path.isabs(source):
                raise MountPointConfException(f"Invalid mount point: source path {source} must be relative and not absolute")

            mounts.add(MountPoint(source, mp, mode!="rw", require_abs_mount_path=False))

        return mounts
