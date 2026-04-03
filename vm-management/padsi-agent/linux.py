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

import logging
import os
import pwd
import subprocess

from common import OSAgent, Runner


class LinuxOSAgent(OSAgent):
    _management_mount_point:str="/run/padsi-agent"

    def __init__(self, logger:logging.Logger):
        super().__init__(logger)

        # mount the virtio filesystem which contains all the resources to manage the VM
        self.logger.info(f"mounting the padsi-agent FS to {self.__class__._management_mount_point}")
        self.virtio_mount("padsi-agent", None, self.__class__._management_mount_point)

        # load the configuration
        self._logger.info("Loading config...")
        self.load_vm_config(os.path.join(self.__class__._management_mount_point, "etc", "config.json"),
                            os.path.join(self.__class__._management_mount_point, "etc", "mountpoints.json"))

        self._user_session_opened=False
        self._has_gui:bool|None=None

    @property
    def management_mount_point(self) -> str:
        return self.__class__._management_mount_point

    @property
    def script_extensions(self) -> list[str]:
        return ["sh", "py"]

    @property
    async def home_dir(self) -> str:
        try:
            return pwd.getpwuid(self.vm_config.user_id).pw_dir
        except Exception as e:
            self.logger.error(f"Could not get user home directory for UID {self.vm_config.user_id}: {str(e)}")
            raise e

    @property
    def has_gui(self) -> bool:
        if self._has_gui is None:
            p=subprocess.run(["systemctl"], capture_output=True, text=True)
            if p.returncode==0:
                self._has_gui=False
                for line in p.stdout.splitlines():
                    if "gdm" in line or "sddm" in line or "lightdm" in line:
                        self._has_gui=True
                        break
            else:
                self._has_gui=False # really, no systemd?

        return self._has_gui

    def get_runner(self, ext:str) -> Runner|None:
        return Runner(None)

    def _virtiofs_mounted(self, mountpoint:str) -> bool:
        proc=subprocess.run(["findmnt", "-n", "-o", "SOURCE", mountpoint], capture_output=True, text=True)
        if proc.returncode!=0:
            if not proc.stderr:
                return False
            raise Exception(f"Could not run findmnt: {proc.stderr}")
        return True

    def virtio_mount(self, fsname:str, config_mountpoint:str|None, real_mountpoint:str):
        if not self._virtiofs_mounted(real_mountpoint):
            if not os.path.exists(real_mountpoint):
                os.makedirs(real_mountpoint)
            args=["mount", "-t", "virtiofs", fsname, real_mountpoint]
            proc=subprocess.run(args, capture_output=True, text=True)
            if proc.returncode!=0:
                raise Exception(f"Could not mount virtiofs named {fsname} to {real_mountpoint}: {proc.stderr if proc.stderr else proc.stdout}")

    @property
    async def user_session_opened(self) -> bool:
        user_runtime_dir=os.path.join("/run", "user", f"{self.vm_config.user_id}")
        if not self._user_session_opened:
            if self.has_gui:
                if os.path.exists(os.path.join(user_runtime_dir, "wayland-0")) or \
                    os.path.exists(os.path.join(user_runtime_dir, "ICEauthority")):
                    self._user_session_opened=True
            else:
                if os.path.exists(user_runtime_dir):
                    self._user_session_opened=True
        self.logger.debug(f"user_session_opened() => {self._user_session_opened} (has UI: {self.has_gui}, user_runtime_dir: {user_runtime_dir})")
        return self._user_session_opened

    def shutdown(self):
        """Shut down the system"""
        subprocess.run(["poweroff"])
