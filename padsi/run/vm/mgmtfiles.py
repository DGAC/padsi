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
# PADSI object to assemble all the management resources of a VM
#

from __future__ import annotations

import grp
import json
import os
import pwd
import shutil
import syslog
import uuid

import padsi.misc
from padsi.config import (MountPoint, VirtualMachine, VMScript, VMUsage, Zone,
                          tap_ip)
from padsi.simple_comm import Client, Message, MessageType

from ..zone_userfiles import ZoneUserFiles
from .version import VMVersion, VMVersionType


def _get_top_source_dir() -> str:
    """Get the actual directory where all PADSI's source code is
    """
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__)))))

class VMManagementFiles:
    """Object which sets up a directory containing all the management resources (bin/, etc/, lib/) for a VM configuration for a specified user
    """
    def __init__(self, zone_conf:Zone, vm_conf:VirtualMachine, vm_version:VMVersion,
                 zone_home_dir:str, uid:int, gid:int, run_dir:str, client:Client|None, discriminant:str|None=None):
        self._vm_conf=vm_conf
        self._zone_conf=zone_conf
        self._vm_version=vm_version
        self._zone_home_dir=zone_home_dir # directory in the host where the $HOME of the user in the zone is
        self._uid=uid
        self._gid=gid
        self._run_dir=run_dir
        self._client=client
        self._discriminant=discriminant if discriminant is not None else str(uuid.uuid4())

        self._syslog_prefix=f"mgmtfiles, VM {vm_conf.id} zone {zone_conf.name}, uid {uid}"

        # directory where all the management files are located. The discriminant at the end is necessary to ensure that each instance is really unique
        # (which is a problem otherwise when a VM is customized right before it is run and the customization routine, occurring _after_ the final
        # VM has been started, wipes the management information of the final VM that has just been started)
        self._mgmt_dir:str=os.path.join(self._run_dir, "vm-management-files", self._zone_conf.name, self._vm_conf.id, self._discriminant)

    @property
    def management_files_dir(self) -> str:
        """Directory in the host where all the management files for the VM are located
        """
        return self._mgmt_dir

    @property
    def zone_home_dir(self) -> str:
        """Directory in the host where all the user files for the zone are.
        """
        return self._zone_home_dir

    def _setup_files(self):
        """Prepare the management files which are not mount points but created or copied
        """
        def _get_real_path(path:str, os_variant:str, top_src_dir:str) -> str|None:
            if os.path.isabs(path):
                return os.path.realpath(path)
            osvpath=os.path.join(top_src_dir, "vm-management", "scripts", os_variant)
            if not os.path.exists(osvpath):
                syslog.syslog(syslog.LOG_ERR, f"OS variant path '{osvpath}' does not exist")
                return None
            return os.path.realpath(os.path.join(osvpath, path))

        # prepare directories
        os.makedirs(self._mgmt_dir, exist_ok=True)
        bin_dir=os.path.join(self._mgmt_dir, "bin")
        etc_dir=os.path.join(self._mgmt_dir, "etc")
        lib_dir=os.path.join(self._mgmt_dir, "lib")

        for path in (bin_dir, etc_dir, lib_dir):
            try:
                os.makedirs(path, exist_ok=True)
            except Exception as e:
                syslog.syslog(syslog.LOG_ERR, f"{self._syslog_prefix}: failed to create directoy '{path}': {str(e)}")
                raise e
        top_src_dir=_get_top_source_dir()

        script:str|None=None
        try:
            # copy on-boot script
            vm_customize:bool=False
            match self._vm_conf.usage:
                case VMUsage.INSTALL:
                    script=None
                case VMUsage.UPDATE:
                    script=self._vm_conf.get_script(VMScript.UPDATE)
                case VMUsage.RUN:
                    match self._vm_version.version_type:
                        case VMVersionType.USER:
                            vm_customize=True
                            script=self._vm_conf.get_script(VMScript.CUSTOMIZE)
                        case VMVersionType.SNAP:
                            script=self._vm_conf.get_script(VMScript.RUN)
                        case _:
                            raise Exception("CODEBUG: situation should not happen")
                case _:
                    raise Exception(f"Unhandled VMUsage {self._vm_conf.usage}")

            if script:
                ext=os.path.splitext(script)[1]
                src=_get_real_path(script, self._vm_conf.os_variant, top_src_dir)
                if src is not None:
                    dst=os.path.join(bin_dir, f"on-boot{ext}")
                    shutil.copyfile(src, dst)
                    shutil.copymode(src, dst)

            # copy on-shutdown script
            script=self._vm_conf.get_script(VMScript.SHUTDOWN)
            if script:
                ext=os.path.splitext(script)[1]
                src=_get_real_path(script, self._vm_conf.os_variant, top_src_dir)
                if src is not None:
                    dst=os.path.join(bin_dir, f"on-shutdown{ext}")
                    shutil.copyfile(src, dst)
                    shutil.copymode(src, dst)

        except Exception as e:
            syslog.syslog(syslog.LOG_ERR, f"{self._syslog_prefix}: failed to copy script '{script}' to VM' management files: {str(e)}")
            raise e

        # create attributes config files
        try:
            userdata=pwd.getpwuid(self._uid)
            try:
                (fname, *_)=userdata.pw_gecos.split(",")
            except Exception:
                fname=userdata.pw_name
            try:
                grname=grp.getgrgid(self._gid).gr_name
            except Exception:
                grname=userdata.pw_name
            attributes:dict[str,str]={
                "PADSI_VM_CONFIG": self._vm_conf.id,
                "PADSI_VM_NAME": str(self._vm_version),
                "PADSI_VM_NICKNAME": self._vm_version.nickname if self._vm_version.nickname is not None else "",
                "PADSI_VM_USAGE": "CUSTOMIZE" if vm_customize else self._vm_conf.usage.value.upper(),
                "PADSI_USER_ID": str(self._uid),
                "PADSI_USER_NAME": userdata.pw_name,
                "PADSI_USER_FULLNAME": fname,
                "PADSI_USER_SHELL": userdata.pw_shell,
                "PADSI_GROUP_ID": str(self._gid),
                "PADSI_GROUP_NAME": grname,
                "PADSI_LANG": os.environ.get("LANG", "C")
            }

            if len(self._zone_conf.web_proxies)>0:
                proxy=f"http://{tap_ip}:3128"
                attributes["PADSI_WEB_PROXY"]=proxy
            with open(os.path.join(etc_dir, "config.txt"), "wt") as fd:
                fd.write("\n".join([f"{key}={value}" for (key, value) in attributes.items()])+"\n")
            with open(os.path.join(etc_dir, "config.json"), "wt") as fd:
                json.dump(attributes, fd)

            if len(self._zone_conf.web_proxies)>0:
                attributes["http_proxy"]=attributes["PADSI_WEB_PROXY"]
                attributes["https_proxy"]=attributes["PADSI_WEB_PROXY"]
            with open(os.path.join(etc_dir, "config.sh"), "wt") as fd:
                fd.write("\n".join([f"export {key}=\"{value}\"" for (key, value) in attributes.items()])+"\n")

            # copy automatically generated SSH key to ETC
            ssh_pubkey_file=os.path.join(self._zone_home_dir, ZoneUserFiles.get_ssh_pubkey_file())
            if os.path.exists(ssh_pubkey_file):
                shutil.copyfile(ssh_pubkey_file, os.path.join(etc_dir, os.path.basename(ZoneUserFiles.get_ssh_pubkey_file())))
        except Exception as e:
            syslog.syslog(syslog.LOG_ERR, f"{self._syslog_prefix}: failed to create VM attributes: {str(e)}")
            raise e

        # create mountpoint config files
        try:
            mountpoints:dict[str,str]={}
            host_user_xdg_subdirectories=padsi.misc.compute_user_xdg_subdirectories(self._uid)
            if self._vm_conf.os_variant=="windows":
                # Windows (as MacOS) does not translate XDG directories but lures the user in the UI
                vm_user_xdg_subdirectories={
                    "DESKTOP": "Desktop",
                    "DOWNLOAD": "Downloads",
                    "DOCUMENTS": "Documents",
                    "MUSIC": "Music",
                    "PICTURES": "Pictures",
                    "VIDEOS": "Videos"
                }
            else:
                vm_user_xdg_subdirectories=host_user_xdg_subdirectories

            for mp in self._vm_conf.mount_points:
                actual_sp=padsi.misc.expand_variables_in_string(mp.source_path, host_user_xdg_subdirectories)
                fsname=actual_sp.replace("/", "_")
                actual_mp=padsi.misc.expand_variables_in_string(mp.mount_path, vm_user_xdg_subdirectories)
                mountpoints[fsname]=actual_mp

            with open(os.path.join(etc_dir, "mountpoints.txt"), "wt") as fd:
                fd.write("\n".join([f"{key}={value}" for (key, value) in mountpoints.items()])+"\n")
            with open(os.path.join(etc_dir, "mountpoints.json"), "wt") as fd:
                json.dump(mountpoints, fd)
        except Exception as e:
            syslog.syslog(syslog.LOG_ERR, f"{self._syslog_prefix}: failed to create VM's mountpoint file: {str(e)}")
            raise e

    def _get_mount_points(self, check_user_dir_exists:bool) -> list[MountPoint]:
        """Get the actual mount points set up for the VM
        """
        res:list[MountPoint]=[]

        # lib/
        lib_dir=os.path.join(self._mgmt_dir, "lib")
        top_src_dir=_get_top_source_dir()
        osvpath=os.path.join(top_src_dir, "vm-management", "scripts", self._vm_conf.os_variant)
        res.append(MountPoint(osvpath, lib_dir, True))

        # user's ~/.padsi-agent/ directory, if it exists is mounted read-only
        padsi_agent_user_dir=os.path.join(self._zone_home_dir, ".padsi-agent")
        syslog.syslog(syslog.LOG_DEBUG, f"{self._syslog_prefix}: user's .padsi-agent dir is {padsi_agent_user_dir}, exists: {os.path.isdir(padsi_agent_user_dir)}")
        if not check_user_dir_exists or os.path.isdir(padsi_agent_user_dir):
            user_dir=os.path.join(self._mgmt_dir, "user")
            os.makedirs(user_dir, exist_ok=True)
            res.append(MountPoint(padsi_agent_user_dir, user_dir, True))

        return res

    #
    # privileges operations
    #
    @property
    def _priv_args(self) -> dict:
        return {
            "vm-id": self._vm_conf.id,
            "vm-usage": self._vm_conf.usage.value,
            "zone-name": self._zone_conf.name,
            "vm-version": self._vm_version.id,
            "zone-home-dir": self._zone_home_dir,
            "discriminant": self._discriminant
        }

    async def setup(self):
        """Set up the VM management files, including using the privileged interface
        for the privileged operations"""
        if self._client is None:
            raise Exception(f"Invalid {self.__class__.__name__} usage")
        self._setup_files()
        self._get_mount_points(True) # force creation of required directories as this user and not later as root

        try:
            _msgc={"cmde": "vm-management-files-setup"}
            _msgc.update(self._priv_args)
            msg=Message(MessageType.REQUEST, _msgc)
            await self._client.call_server(msg)
        except Exception as e:
            syslog.syslog(syslog.LOG_ERR, f"{self._syslog_prefix}: failed to setup files: {str(e)}")
            raise e

    @staticmethod
    def priv_setup(zone_conf:Zone, vm_conf:VirtualMachine, vm_version:VMVersion,
                   zone_home_dir:str, uid:int, gid:int, run_dir:str, discriminant:str) -> list[str]:
        """Privileged operation: mount the required directories
        """
        vmm=VMManagementFiles(zone_conf=zone_conf, vm_conf=vm_conf, vm_version=vm_version,
                              zone_home_dir=zone_home_dir, uid=uid, gid=gid, run_dir=run_dir, client=None, discriminant=discriminant)
        syslog.syslog(syslog.LOG_DEBUG, f"{vmm._syslog_prefix}: setup")
        mounted:list[MountPoint]=[]
        try:
            for mp in vmm._get_mount_points(True):
                syslog.syslog(syslog.LOG_INFO, f"{vmm._syslog_prefix}: mounting {mp.source_path} to {mp.mount_path}")
                if mp.mount():
                    mounted.append(mp)
            syslog.syslog(syslog.LOG_DEBUG, f"{vmm._syslog_prefix}: setup done")
        except Exception as e:
            for mp in mounted[::-1]:
                syslog.syslog(syslog.LOG_INFO, f"{vmm._syslog_prefix}: unmounting {mp.mount_path} from {mp.source_path}")
                try:
                    mp.umount()
                except Exception as se:
                    syslog.syslog(syslog.LOG_WARNING, f"{vmm._syslog_prefix}: {str(se)}")
            syslog.syslog(syslog.LOG_ERR, f"{vmm._syslog_prefix}: setup failed: {str(e)}")
        return [mp.mount_path for mp in mounted[::-1]]

    async def cleanup(self):
        """Cleanup"""
        if self._client is None:
            raise Exception(f"Invalid {self.__class__.__name__} usage")

        try:
            _msgc={"cmde": "vm-management-files-cleanup"}
            _msgc.update(self._priv_args)
            msg=Message(MessageType.REQUEST, _msgc)
            await self._client.call_server(msg)

            # we only use rmtree() when we are sure all the user's mounted directories have been unmounted to avoid data loss
            try:
                shutil.rmtree(self._mgmt_dir)
            except Exception as e:
                syslog.syslog(syslog.LOG_ERR, f"{self._syslog_prefix}: failed to cleanup VM's management files in {self._mgmt_dir}: {str(e)}")
        except Exception as e:
            syslog.syslog(syslog.LOG_ERR, f"{self._syslog_prefix}: failed to cleanup files: {str(e)}")

    @staticmethod
    def priv_cleanup(zone_conf:Zone, vm_conf:VirtualMachine, vm_version:VMVersion,
                     zone_home_dir:str, uid:int, gid:int, run_dir:str, discriminant:str):
        """Privileged operation: clean up the mount points"""
        vmm=VMManagementFiles(zone_conf=zone_conf, vm_conf=vm_conf, vm_version=vm_version,
                              zone_home_dir=zone_home_dir, uid=uid, gid=gid, run_dir=run_dir, client=None, discriminant=discriminant)
        syslog.syslog(syslog.LOG_DEBUG, f"{vmm._syslog_prefix}: cleanup")
        revmp=vmm._get_mount_points(False)[::-1]
        for mp in revmp:
            try:
                syslog.syslog(syslog.LOG_INFO, f"{vmm._syslog_prefix}: unmounting {mp.mount_path} from {mp.source_path} (actually mounted: {mp.is_mounted()})")
                if mp.is_mounted():
                    mp.umount()
            except Exception as e:
                syslog.syslog(syslog.LOG_WARNING, f"{vmm._syslog_prefix}: {str(e)}")
                raise e
        syslog.syslog(syslog.LOG_DEBUG, f"{vmm._syslog_prefix}: cleanup done")
