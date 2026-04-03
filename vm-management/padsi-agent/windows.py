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

import asyncio
import logging
import os
import shlex
import subprocess
import time

from common import OSAgent, Runner


def _remove_links(path):
    """Recursively remove any link found in the specified path
    """
    for (dirpath, dirnames, filenames) in os.walk(path, topdown=False):
        for fname in filenames:
            spath=os.path.join(dirpath, fname)
            if os.path.islink(spath):
                print(f"Removing link {spath}")
                os.remove(spath)

class WindowsOSAgent(OSAgent):
    _management_mount_point_raw:str="Z:"
    _management_mount_point:str="Z:\\" # needed for os.path.join()

    def __init__(self, logger:logging.Logger):
        super().__init__(logger)
        self._home_dir:str|None=None
        self._WinFsp_started:bool=False
        self._last_drive_used:str|None=None # last drive letter used

         # mount the virtio filesystem which contains all the resources to manage the VM
        self.logger.info(f"mounting the padsi-agent FS to {self.__class__._management_mount_point}")
        self.virtio_mount("padsi-agent", self.__class__._management_mount_point_raw, self.__class__._management_mount_point_raw)

        # load the configuration
        self.logger.info("Loading config...")
        self.load_vm_config(os.path.join(self.__class__._management_mount_point, "etc", "config.json"),
                            os.path.join(self.__class__._management_mount_point, "etc", "mountpoints.json"))

        self._user_session_opened=False

        self._user_session_task=asyncio.create_task(self._check_for_user_session_opened())

    @property
    def management_mount_point(self) -> str:
        return self.__class__._management_mount_point

    @property
    def script_extensions(self) -> list[str]:
        return ["ps1", "bat"]

    @property
    async def home_dir(self) -> str:
        if self._home_dir is None:
            self._home_dir=os.path.join(r"C:\Users", self.vm_config.user_name)
            # wait for the home directory te actually be created (it might not be the case when the user has just been created)
            while True:
                if os.path.exists(self._home_dir):
                    break
                self.logger.info(f"HOME dir {self._home_dir} does not yet exist...")
                await asyncio.sleep(1)

            # Windows links might cause problems later when mounting drives and are seldom used so we can
            # safely remove them now
            _remove_links(self._home_dir)
        return self._home_dir

    @property
    def has_gui(self) -> bool:
        return True

    def get_runner(self, ext:str) -> Runner|None:
        match ext.lower():
            case "bat":
                return Runner(["cmd", "/c"])
            case "ps1":
                return Runner(["powershell"])
            case _:
                return Runner(None)

    def _virtiofs_mapped_drive(self, fsname:str) -> str|None:
        """Tell if a specific virtiofs drive is actually mapped and its mapped drive (like "Z:"), or None
        """
        args=[r"C:\Program Files (x86)\WinFsp\bin\launchctl-x64.exe", "list"]
        self.logger.info(f"Running: {' '.join(args)}")
        proc=subprocess.run(args, text=True, capture_output=True)
        if proc.returncode!=0:
            msg=f"Could not list WinFsp mapped drives: {proc.stderr if proc.stderr else proc.stdout}"
            self.logger.error(msg)
            raise Exception(msg)

        # proc.stdout will be like:
        # OK
        # virtiofs viofsZ
        # virtiofs viofsY
        for line in proc.stdout.splitlines():
            parts=line.split()
            if len(parts)==2 and parts[0]=="virtiofs" and len(parts[1])==6 and parts[1].startswith("viofs"):
                drive_letter=parts[1][5]
                args=[r"C:\Program Files (x86)\WinFsp\bin\launchctl-x64.exe", "info", "virtiofs", parts[1]]
                self.logger.info(f"Running: {' '.join(args)}")
                sproc=subprocess.run(args, text=True, capture_output=True)
                if sproc.returncode!=0:
                    msg=f"Could not get info about WinFsp mapped drive '{drive_letter}': {sproc.stderr if sproc.stderr else sproc.stdout}"
                    self.logger.error(msg)
                    raise Exception(msg)

                # sproc.stdout will be like:
                # OK
                # virtiofs viofsX
                # "C:\Program Files\Virtio-Win\VioFS\virtiofs.exe" -t "Tools_PADSI_vm-management" -m "X:"
                lines=sproc.stdout.splitlines()
                if lines[0]!="OK" or len(lines)<3:
                    msg=f"Unexpected info output about WinFsp mapped drive '{drive_letter}': {sproc.stdout}"
                    print(f"==> [{lines[0]}]")
                    self.logger.error(msg)
                    raise Exception(msg)

                cmdparts=shlex.split(lines[2])
                if len (cmdparts)!=5:
                    msg=f"Unexpected info output about WinFsp mapped drive '{drive_letter}': {lines[2]}"
                    self.logger.error(msg)
                    raise Exception(msg)
                if cmdparts[2]==fsname:
                    return cmdparts[4]
        return None

    def virtio_mount(self, fsname:str, config_mountpoint:str|None, real_mountpoint:str):
        if config_mountpoint is None:
            msg="CODEBUG: config_mountpoint should not be None for Windows"
            self.logger.error(msg)
            raise Exception(msg)

        drive=self._virtiofs_mapped_drive(fsname)
        if drive is None:
            # run WinFsp if not yet done
            if not self._WinFsp_started:
                args=[r"C:\Program Files (x86)\WinFsp\bin\fsreg.bat", "virtiofs", r"C:\Program Files\Virtio-Win\VioFS\virtiofs.exe", "-t %1 -m %2"]
                self.logger.info(f"Running: {' '.join(args)}")
                proc=subprocess.run(args, text=True, capture_output=True)
                if proc.returncode!=0:
                    msg=f"Could not start WinFsp: {proc.stderr if proc.stderr else proc.stdout}"
                    self.logger.error(msg)
                    raise Exception(msg)
                self._WinFsp_started=True

            # get next available drive letter
            if self._last_drive_used is None:
                drive_letter="Z"
            else:
                drive_letter=chr(ord(self._last_drive_used[0])-1)
            drive=f"{drive_letter}:"

            if drive_letter=="C":
                raise Exception("No more drive letter available")
            if len(config_mountpoint)==2 and config_mountpoint[1]==":":
                # config_mountpoint was specified as a letter
                if config_mountpoint!=drive:
                    raise Exception(f"Not handled: mounting drive {drive} to {config_mountpoint}")

            args=[r"C:\Program Files (x86)\WinFsp\bin\launchctl-x64.exe", "start", "virtiofs", f"viofs{drive_letter}", fsname, drive]
            self.logger.info(f"Running: {' '.join(args)}")
            proc=subprocess.run(args, capture_output=True, text=True)
            if proc.returncode!=0:
                msg=f"Could not mount '{fsname}' as drive {drive}: {proc.stderr if proc.stderr else proc.stdout}"
                self.logger.error(msg)
                raise Exception(msg)

            self._last_drive_used=drive
            self.logger.info(f"VirtioFS '{fsname}' will soon be mapped to {drive}, waiting a bit")

            # wait for the drive to actually be mapped, it may take some time
            while True:
                time.sleep(0.25)
                if os.path.exists(drive):
                    break
            self.logger.info(f"VirtioFS '{fsname}' is now mapped to {drive}")
        else:
            self.logger.info(f"VirtioFS '{fsname}' is already mapped to {drive}")

        # bind to final mount point
        if not os.path.exists(real_mountpoint):
            os.makedirs(real_mountpoint)
        if config_mountpoint!=drive:
            self.logger.info(f"Linking drive {drive} to {config_mountpoint}")

            # the link point must not yet exist
            try:
                os.rmdir(real_mountpoint)
            except PermissionError:
                args=["cmd", "/c", "rmdir", "/Q", "/S", real_mountpoint]
                self.logger.info(f"Running: {' '.join(args)}")
                proc=subprocess.run(args, capture_output=True, text=True)
                if proc.returncode!=0:
                    msg=f"Could not remove directory '{real_mountpoint}': {proc.stderr if proc.stderr else proc.stdout}"
                    self.logger.error(msg)
                    raise Exception(msg)

            # actually create the link
            args=["cmd", "/c", "mklink", "/d", real_mountpoint, drive]
            self.logger.info(f"Running: {' '.join(args)}")
            proc=subprocess.run(args, capture_output=True, text=True)
            if proc.returncode!=0:
                msg=f"Could not link '{real_mountpoint}' to drive {drive}: {proc.stderr if proc.stderr else proc.stdout}"
                self.logger.error(msg)
                raise Exception(msg)

    def _is_user_session_opened(self) -> bool:
        # call the user-session-opened.ps1 script
        runner=self.get_runner("ps1")
        if runner is None:
            msg="CODEBUG: runner should not be None"
            self.logger.error(msg)
            raise Exception(msg)

        script=os.path.join(os.path.dirname(__file__), "user-session-opened.ps1")
        cenv=os.environ.copy()
        cenv["PADSI_USER_NAME"]=self.vm_config.user_name
        proc=subprocess.run(runner.get_arguments([script]), capture_output=True, text=True, env=cenv)
        if proc.returncode==0:
            return proc.stdout.strip().lower()=="true"
        else:
            msg=f"Failed to execute '{script}: {proc.stderr}"
            self.logger.error(msg)
            raise Exception(msg)

    async def _check_for_user_session_opened(self):
        loop=asyncio.get_event_loop()
        while not self._user_session_opened:
            try:
                self._user_session_opened=await loop.run_in_executor(None, self._is_user_session_opened)
            except Exception:
                break
        self._user_session_task=None

    @property
    async def user_session_opened(self) -> bool:
        return self._user_session_opened

    def shutdown(self):
        """Shut down the system"""
        subprocess.run(["powershell", "Stop-Computer", "-Force"])
