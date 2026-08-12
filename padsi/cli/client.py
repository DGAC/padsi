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
# PADSI client base API implementation
#

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from urllib import parse

import padsi.run
import requests.exceptions
import requests_unixsocket


@dataclass
class VMVersionsSet:
    """Per user VM versions"""
    base_staged:str|None
    user_versions:list[str]
    snapshot_versions:list[str]

    @classmethod
    def from_data(cls, data:dict) -> VMVersionsSet:
        return cls(data["staged"], data["user-versions"], data["snapshot-versions"])

    def serialize(self) -> dict:
        return {
            "staged": self.base_staged,
            "user-versions": self.user_versions,
            "snapshot-versions": self.snapshot_versions
        }

@dataclass
class VMVersionInfo:
    dependencies: str
    parent: str|None
    children: list[str]
    state: str|None
    nickname: str|None
    history: list[str]|None
    qemu_pid: int|None
    qemu_ns: str|None
    zone: str|None

    @classmethod
    def from_data(cls, data:dict):
        return cls(data["dependencies"], data["parent"], data["children"], data["state"], data["nickname"], data["history"],
                   data["qemu-pid"], data["qemu-ns"], data["zone"])

    def serialize(self) -> dict:
        return {
            "dependencies": self.dependencies,
            "parent": self.parent,
            "children": self.children,
            "state": self.state,
            "nickname": self.nickname,
            "history": self.history,
            "qemu-pid": self.qemu_pid,
            "qemu-ns": self.qemu_ns,
            "zone": self.zone
        }

@dataclass
class VMStatus:
    vm_id: str
    directory_exists:bool
    base_vm_versions:list[str]
    vm_versions:VMVersionsSet # for the current user
    other_vm_versions:dict[int,VMVersionsSet] # for the other users
    committable_vm_versions:list[str]
    obsolete_vm_versions:list[str]
    unused_files:list[str]
    infos_vm_versions:dict[str,VMVersionInfo]

    @classmethod
    def from_data(cls, data:dict) -> VMStatus:
        vm_id=data["vm-id"]
        directory_exists=data["directory-exists"]
        base_vm_versions=data["base-vm-versions"]
        vm_versions=VMVersionsSet.from_data(data["self-vm-versions"])
        other_vm_versions={}
        for (uid, sdata) in data["other-vm-versions"].items():
            other_vm_versions[uid]=VMVersionsSet.from_data(sdata)
        committable_vm_versions=data["commitable-vm-versions"]
        obsolete_vm_versions=data["obsolete-vm-versions"]
        unused_files=data["unused-files"]

        infos={}
        for (k,v) in data["vm-versions-infos"].items():
            infos[k]=VMVersionInfo.from_data(v)

        return cls(vm_id, directory_exists, base_vm_versions, vm_versions, other_vm_versions, committable_vm_versions,
                   obsolete_vm_versions, unused_files, infos)

    def serialize(self) -> dict:
        return {
            "vm-id": self.vm_id,
            "directory-exists": self.directory_exists,
            "base-vm-versions": self.base_vm_versions,
            "self-vm-versions": self.vm_versions.serialize(),
            "other-vm-versions": {k:v.serialize() for (k,v) in self.other_vm_versions.items()},
            "committable-vm-versions": self.committable_vm_versions,
            "obsolete-vm-versions": self.obsolete_vm_versions,
            "unused-files": self.unused_files,
            "vm-version-infos": {k:v.serialize() for (k,v) in self.infos_vm_versions.items()}
        }

    def get_vm_version_id(self, vmversion:str) -> str:
        parts=vmversion.split(":")
        if len(parts)==1:
            return parts[0]
        elif len(parts)>2:
            raise Exception(f"CODEBUG: invalid vmversion format '{vmversion}': contains more than on colon separator")
        return parts[1]

    def get_vm_version_display_name(self, vmversion:str) -> str:
        infos:VMVersionInfo|None=self.infos_vm_versions.get(vmversion)
        if infos is None:
            raise Exception(f"Unknown VM version '{vmversion}'")
        return self.get_vm_version_id(vmversion) if infos.nickname is None else f"{infos.nickname} ({self.get_vm_version_id(vmversion)})"

    def get_vm_version_description(self, vmversion:str, zone_name:str|None, restricted_view:bool=False, with_deps:bool=True) -> str:
        infos:VMVersionInfo|None=self.infos_vm_versions.get(vmversion)
        if infos is None:
            raise Exception(f"Unknown VM version '{vmversion}'")

        parts:list[str]=[]
        if with_deps:
            if infos.dependencies is not None:
                parts.append(infos.dependencies)
            if infos.state is not None:
                parts.append(infos.state)
        if infos.zone is not None and not restricted_view and infos.zone!=zone_name:
            parts.append(f"in zone '{infos.zone}'")
        return ", ".join(parts)

    def get_vm_version_history(self, vmversion:str) -> list[str]|None:
        infos:VMVersionInfo|None=self.infos_vm_versions.get(vmversion)
        if infos is None:
            raise Exception(f"Unknown VM version '{vmversion}'")
        return infos.history

class NoServiceException(Exception):
    pass

class BaseClient:
    """Object to interact with a PADSI service using a Unix socket"""
    timeout=30

    def __init__(self, socket_path:str):
        self._socket=socket_path
        if not os.path.exists(self._socket):
            raise NoServiceException("The PADSI user service does not appear to be running (or the -U flag must be used)")
        self._q_socket=parse.quote_plus(self._socket)
        self._session=requests_unixsocket.Session()

    def get(self, path:str, params:dict|None=None):
        if path[0]!="/":
            raise Exception(f"Invalid path '{path}'")
        try:
            resp=self._session.get(f"http+unix://{self._q_socket}{path}", params=params, timeout=BaseClient.timeout)
        except requests.exceptions.ConnectionError:
            raise Exception("PADSI service connection refused")
        if resp.ok:
            return self._handle_response_generated_exception(resp.json())
        raise Exception(resp.text)

    def post(self, path:str, data):
        if path[0]!="/":
            raise Exception(f"Invalid path '{path}'")
        try:
            resp=self._session.post(f"http+unix://{self._q_socket}{path}", data=json.dumps(data), headers={"Content-Type": "application/json"},
                                    timeout=BaseClient.timeout)
        except requests.exceptions.ConnectionError:
            raise Exception("PADSI service connection refused")
        if resp.ok:
            return self._handle_response_generated_exception(resp.json())
        raise Exception(resp.text)

    def put(self, path:str, data):
        if path[0]!="/":
            raise Exception(f"Invalid path '{path}'")
        try:
            resp=self._session.put(f"http+unix://{self._q_socket}{path}", data=json.dumps(data), headers={"Content-Type": "application/json"},
                                timeout=BaseClient.timeout)
        except requests.exceptions.ConnectionError:
            raise Exception("PADSI service connection refused")
        if resp.ok:
            return self._handle_response_generated_exception(resp.json())
        raise Exception(resp.text)

    def delete(self, path:str, data):
        if path[0]!="/":
            raise Exception(f"Invalid path '{path}'")
        try:
            resp=self._session.delete(f"http+unix://{self._q_socket}{path}", data=json.dumps(data), headers={"Content-Type": "application/json"},
                                    timeout=BaseClient.timeout)
        except requests.exceptions.ConnectionError:
            raise Exception("PADSI user service connection failed")
        if resp.ok:
            return self._handle_response_generated_exception(resp.json())
        raise Exception(resp.text)

    def _handle_response_generated_exception(self, data):
        if data is None:
            return None
        if isinstance(data, dict):
            exp=data.get("exception")
            if exp is not None:
                raise Exception(exp)
        return data

def print_vm_status(status:VMStatus, zone_name:str|None, verbose:bool, use_json:bool, indent:str="    ", restricted_view:bool=False):
    if use_json:
        data=status.serialize()
        print(f"{json.dumps(data, indent=4)}")
        return

    is_admin=os.geteuid()<1000
    base_versions=status.base_vm_versions
    user_versions=status.vm_versions.user_versions
    snap_versions=status.vm_versions.snapshot_versions
    if len(base_versions)>0 or len(user_versions)>0 or len(snap_versions)>0 or status.vm_versions.base_staged is not None:
        print("VM Versions:")
        if status.vm_versions.base_staged is not None:
            print(f"{indent}staged ({status.vm_versions.base_staged})")
        for vmversion in base_versions+user_versions+snap_versions:
            name=status.get_vm_version_display_name(vmversion)
            descr=status.get_vm_version_description(vmversion, zone_name, restricted_view=restricted_view)
            if descr is not None:
                print(f"{indent}{name}: {descr}")
            else:
                print(f"{indent}{name}")
            if verbose:
                hist=status.get_vm_version_history(vmversion)
                if hist is not None:
                    for event in hist:
                        print(f"{indent*2}{event}")

    if not is_admin and len(base_versions)==0:
        if status.vm_versions.base_staged is None or status.vm_versions.base_staged!=padsi.run.VMState.STOPPED:
            print("No base VM version defined yet, use 'padsi-cli vm-install' to create one")
        else:
            print("No base VM version defined yet, use 'padsi-cli vm-publish' to publish the staged version")

    # for other users
    for (uid, userset) in status.other_vm_versions.items():
        header_done=False
        user_versions=userset.user_versions
        snap_versions=userset.snapshot_versions

        if userset.base_staged is not None:
            if not header_done:
                print(f"For user {uid:}")
                header_done=True
            print(f"{indent}{uid}/staged ({userset.base_staged})")
        for vmversion in user_versions+snap_versions:
            name=status.get_vm_version_display_name(vmversion)
            descr=status.get_vm_version_description(vmversion, zone_name, restricted_view=restricted_view)
            if not header_done:
                print(f"For user {uid:}")
                header_done=True
            if descr is not None:
                print(f"{indent}{name}: {descr}")
            else:
                print(f"{indent}{name}")


    # possible commits
    if len (status.committable_vm_versions)>0:
        print("VM versions which can be merged:")
        for version in status.committable_vm_versions:
            print(f"{indent}{version}")
    else:
        print("No VM version can be merged")

    # obsolete files
    if len (status.obsolete_vm_versions)>0:
        print("VM versions which should be discarded:")
        for version in status.obsolete_vm_versions:
            st=status.get_vm_version_description(version, zone_name, restricted_view=restricted_view, with_deps=False)
            print(f"{indent}{status.get_vm_version_id(version)} {st}")
    else:
        print("No VM version should be discarded")

    # unused files
    if len (status.unused_files)>0:
        print("Unused files:")
        for fname in status.unused_files:
            print(f"{indent}{fname}")
