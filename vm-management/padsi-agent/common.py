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

import json
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass

admin_port=12
user_port=3012

@dataclass
class VMConfig:
    """Represents a configuration in the VM, normally loaded from the "config.json" file
    """
    vm_config:str
    vm_name:str
    vm_nickname:str
    vm_usage:str
    user_id:int
    user_name:str
    user_fullname:str
    user_shell:str
    group_id:int
    group_name:str
    lang:str
    mountpoints:dict[str,str]

    @classmethod
    def new_from_config_files(cls, config_file:str, mountpoints_file:str) -> VMConfig:
        while True:
            with open(config_file, "r") as cfd:
                config=json.load(cfd)
                with open(mountpoints_file, "r") as mfd:
                    mounts=json.load(mfd)
                    break
        return cls(config["PADSI_VM_CONFIG"], config["PADSI_VM_NAME"], config["PADSI_VM_NICKNAME"], config["PADSI_VM_USAGE"],
                   int(config["PADSI_USER_ID"]), config["PADSI_USER_NAME"], config["PADSI_USER_FULLNAME"], config["PADSI_USER_SHELL"],
                   int(config["PADSI_GROUP_ID"]), config["PADSI_GROUP_NAME"], config["PADSI_LANG"], mounts)

class Runner:
    """Class to use an external program to start some scripts, probably only necessary
    on the Windows platform
    """
    def __init__(self, prepend_args:list[str]|None):
        self._prepend_args=prepend_args

    def get_arguments(self, args:list[str]|None) -> list[str]:
        if self._prepend_args is None:
            return args if args is not None else []
        return self._prepend_args+args if args is not None else []

class OSAgent(ABC):
    def __init__(self, logger:logging.Logger):
        self._logger=logger
        self._vm_config:VMConfig|None=None

    def load_vm_config(self, config_file:str, mountpoints_file:str):
        """To be called at some point by sub classes
        """
        counter=0
        while True:
            try:
                self._vm_config=VMConfig.new_from_config_files(config_file=config_file, mountpoints_file=mountpoints_file)
                return
            except Exception as e:
                counter+=1
                self._logger.info(f"Failed to open either '{config_file}' or '{mountpoints_file}' (counter is {counter}): {str(e)}")
                if counter==10:
                    msg="Too many attempts failed"
                    self._logger.error(msg)
                    raise Exception(msg)
                time.sleep(0.25)

    @property
    @abstractmethod
    def management_mount_point(self) -> str:
        """Get the mount point of the management data, which corresponds
        to the "padsi-agent" filesystem name
        """
        ...

    @property
    def vm_config(self) -> VMConfig:
        if self._vm_config is None:
            msg="CODEBUG: self.vm_config should not be None"
            self.logger.error(msg)
            raise Exception(msg)
        return self._vm_config

    @property
    def logger(self) -> logging.Logger:
        return self._logger

    @property
    @abstractmethod
    def script_extensions(self) -> list[str]:
        """List of recognized scripts' extensions
        """
        ...

    @property
    @abstractmethod
    async def home_dir(self) -> str:
        """User's home directory like /home/john.doe or C:\\Users\\john.doe
        """
        ...

    @property
    @abstractmethod
    def has_gui(self) -> bool:
        """Tell if the VM actually has a GUI or is a TUI
        """
        ...

    @abstractmethod
    def get_runner(self, ext:str) -> Runner|None:
        """Get the Runner object to run scripts of the specified extension
        """
        ...

    @abstractmethod
    def virtio_mount(self, fsname:str, config_mountpoint:str|None, real_mountpoint:str):
        """Mount a filesystem by its name to the specified real_mountpoint.
        The config_mountpoint is a helper which is the mount point defined in th configuration
        """
        ...

    @property
    @abstractmethod
    async def user_session_opened(self) -> bool:
        """Tell if the user's session has been opened
        """
        ...

    @abstractmethod
    def shutdown(self):
        """Shut the system down
        """
        ...
