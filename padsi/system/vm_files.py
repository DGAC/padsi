#
# Copyright (c) 2026 DGAC/DSNA
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
import padsi.misc
from padsi.config import Configuration, VirtualMachine, VMUsage
from padsi.run import (AdminVMFiles, VMVersion, VMVersionInfo, VMVersionType)

_debug=False

def _vm_version_key(vmversion: VMVersion) -> str:
    return f"{vmversion.zone_name}:{str(vmversion)}" if vmversion.zone_name is not None else str(vmversion)

def _vm_versions_list_to_str_list(vmversions:list[VMVersion], zone_name:str|None) -> list[str]:
    return [_vm_version_key(x) for x in vmversions if _vm_version_keep(x, zone_name)]

def _vm_version_keep(vmversion: VMVersion, zone_name:str|None) -> bool:
    return zone_name is None or vmversion.zone_name is None or zone_name==vmversion.zone_name

def _state_to_str(vmversion:VMVersion|None) -> str|None:
    if vmversion is None:
        return None
    if vmversion.is_complete:
        state=vmversion.state
        return state.value if state else "unknown state"
    else:
        return None

async def _format_status(uid:int, vm:VirtualMachine, vm_id:str, avmf:AdminVMFiles, verbose:bool, zone_name:str|None):
    other_vm_versions:dict={}
    if vm.is_user_allowed(uid):
        for ouid in avmf.users:
            if ouid!=uid:
                staged=avmf.get_staged(VMVersionType.BASE, ouid)
                other_vm_versions[ouid]={
                    "staged": _state_to_str(staged),
                    "user-versions": _vm_versions_list_to_str_list(avmf.user_versions(ouid), zone_name),
                    "snapshot-versions": _vm_versions_list_to_str_list(avmf.snapshot_versions(ouid), zone_name)
                }

    infos={}
    for vmversion in avmf.all_versions:
        info:VMVersionInfo=avmf.get_version_info(vmversion)
        state=vmversion.state
        qemu_pid=vmversion.get_qemu_pid()
        if qemu_pid is None:
            qemu_ns=None
        else:
            qemu_ns=f"{padsi.misc.get_mnt_namespace(qemu_pid)}{padsi.misc.get_net_namespace(qemu_pid)}"
        if _vm_version_keep(vmversion, zone_name):
            infos[_vm_version_key(vmversion)]={
                "dependencies": info.dependencies,
                "parent": _vm_version_key(info.parent) if info.parent is not None else None,
                "children": [_vm_version_key(v) for v in info.children if _vm_version_keep(v, zone_name)],
                "state": None if state is None else state.value,
                "qemu-pid": qemu_pid,
                "qemu-ns": qemu_ns,
                "zone": vmversion.zone_name,
                "nickname": vmversion.nickname,
                "history": [str(event) for event in vmversion.get_history(avmf.get_parent_version(vmversion))] if verbose else []
            }

    return {
        "vm-id": vm_id,
        "directory-exists": True,
        "base-vm-versions": _vm_versions_list_to_str_list(avmf.base_versions, zone_name),
        "self-vm-versions": {
            "staged": _state_to_str(avmf.get_staged(VMVersionType.BASE, uid)),
            "user-versions": _vm_versions_list_to_str_list(avmf.user_versions(uid), zone_name),
            "snapshot-versions": _vm_versions_list_to_str_list(avmf.snapshot_versions(uid), zone_name)
        },
        "other-vm-versions": other_vm_versions,
        "commitable-vm-versions": _vm_versions_list_to_str_list(avmf.committable_versions, zone_name),
        "obsolete-vm-versions": _vm_versions_list_to_str_list(avmf.obsolete_versions, zone_name),
        "unused-files": list(avmf.unused_files),
        "vm-versions-infos": infos
    }

def _compute_avmf(vm_dir:str, uid:int) -> AdminVMFiles:
    return AdminVMFiles(vm_dir, uid)

async def vm_files_analyse(gconf: Configuration, uid:int, vm_id:str, verbose:bool, zone_name:str|None) -> dict:
    vm:VirtualMachine|None=None
    for usage in VMUsage:
        vm_conf=gconf.get_vm(usage, vm_id)
        if vm_conf is not None and vm_conf.is_user_allowed(uid):
            vm=vm_conf
            break
    if vm is None:
        raise Exception(f"Unknown virtual machine '{vm_id}' or not enough privileges")

    loop=asyncio.get_event_loop()
    res=await loop.run_in_executor(None, _compute_avmf, vm.directory, uid)
    if isinstance(res, Exception):
        raise res

    return await _format_status(uid, vm, vm_id, res, verbose, zone_name)
