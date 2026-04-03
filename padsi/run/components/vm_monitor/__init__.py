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
# This is the PADSI component to monitor a running VM
#

import os

import nsbubble

from .. import Component


class VMMonitor(Component):
    """Monitor a VM"""

    def __init__(self):
        self._started=False

    def get_mountpoints(self) -> dict:
        """Get the mount points required by the component
        Cf. nsbubble's documentation for the formalism
        """
        return {
            f"{os.path.dirname(__file__)}/vm-monitor.py": {
                "mount-point": "/tmp/vm-monitor.py",
                "read-only": True,
                "monitored": False
            }
        }

    def start_monitor(self, api:nsbubble.BubbleAPI, image_file:str, vars_file:str, infos_file:str, qemu_pid:int, viewer_pid:int|None):
        """Actually start the required processes in a bubble using the api object
        """
        if self._started:
            return
        api.start_process(["/tmp/vm-monitor.py", image_file, vars_file, infos_file, str(qemu_pid), "NODISPLAY" if viewer_pid is None else str(viewer_pid)],
                           ignore_status=False, required=True, extra_env={
                            "PYTHONPATH": "/usr/share/padsi"
                           })
        self._started=True
