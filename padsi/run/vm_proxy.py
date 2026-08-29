#
# Proxy like which seats between the various services (user service, admin service and zone service) and the system service
# which performs privileged operations on VM files
#

import asyncio
import json
import os
import syslog
from collections.abc import Callable

import aiofiles

from padsi.config import Configuration, VirtualMachine, VMUsage
from padsi.run import AdminVMFiles, VMFiles, VMVersion, VMVersionType, parse_vm_version


class VMProxyException(Exception):
    pass


async def create_vm_dirs(sys_call: Callable, vm_id: str, usage:VMUsage):
    # ensure VM's common directories are created
    await sys_call(
        {
            "cmde": "vm-create-common-dirs",
            "vm-id": vm_id,
            "usage": usage,
        }
    )

    # create the staging directory for the user
    await sys_call(
        {
            "cmde": "vm-create-stage-dir",
            "vm-id": vm_id,
            "usage": usage,
        }
    )


async def stage_imported_files(
    gconf: Configuration,
    sys_call: Callable,
    vm_id: str,
    uid: int,
    gid: int,
    hdd_file: str,
    vars_file: str,
    message: str | None,
) -> VMVersion:
    vm_conf = gconf.get_vm(VMUsage.INSTALL, vm_id)
    if vm_conf is None:
        raise VMProxyException(f"Unknown VM {vm_id}")
    vm_conf.check_user_allowed(uid)
    await create_vm_dirs(sys_call, vm_id, VMUsage.INSTALL)

    vm_files = VMFiles(vm_conf.directory, uid, gid, analyse=False)

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None, vm_files.stage_imported_files, hdd_file, vars_file, message
    )


async def vm_load(
    gconf: Configuration,
    sys_call: Callable,
    vm_id: str,
    uid: int,
    gid: int,
    extract_id: str,
    message: str | None,
) -> VMVersion:
    vm_conf = gconf.get_vm(VMUsage.INSTALL, vm_id)
    if vm_conf is None:
        raise VMProxyException(f"Unknown VM {vm_id}")
    vm_conf.check_user_allowed(uid)
    await create_vm_dirs(sys_call, vm_id, VMUsage.INSTALL)

    # analyse and use the available files
    vm_files = VMFiles(vm_conf.directory, uid, gid, analyse=False)
    extract_dir = os.path.join(vm_files.staging_directory, extract_id)
    if not os.path.isdir(extract_dir):
        raise VMProxyException(f"Resources directory '{extract_dir}' does not exist")

    # analyse directory where the extracted files are to determine a target VM version and return
    # it at this stage to the user before actually loading VM versions
    try:
        nb_vm_versions = 0
        # VM versions to load
        for fname in os.listdir(extract_dir):
            if fname.startswith("vm-version-"):
                nb_vm_versions += 1
        # manifest
        async with aiofiles.open(os.path.join(extract_dir, "manifest.json"), "r") as fd:
            contents = await fd.read()
            manifest = json.loads(contents)
            if manifest.get("dependency-size") is not None:
                nb_vm_versions += 1
    except Exception as e: # noqa: BLE001
        syslog.syslog(syslog.LOG_ERR, f"Invalid saved VM versions' files structure or contents: {e}",)
        raise VMProxyException("Invalid saved VM versions' files structure or contents")

    # prepare VM version which will be returned
    vm_version = VMVersion(
        VMVersionType.BASE,
        vm_files.directory,
        vm_files.next_base_version_number + nb_vm_versions - 1,
    )

    # create an asyncio task to import the VM
    try:
        await sys_call(
            {
                "cmde": "vm-load",
                "vm-id": vm_conf.id,
                "extract-id": extract_id,
                "message": message,
            }
        )
    except Exception as e: # noqa: BLE001
        syslog.syslog(syslog.LOG_ERR, f"Failed to load VM versions: {e}")

    return vm_version


def _get_vm_conf_for_usage(
    gconf: Configuration, vm_id: str, uid: int, usages: list[VMUsage] | None
) -> VirtualMachine:
    """Get the VM configuration for the specified usage(s), and check is user is actually allowed that usage"""
    for usage in VMUsage if usages is None else usages:
        vm_conf = gconf.get_vm(usage, vm_id)
        if vm_conf is None:
            raise VMProxyException(
                f"Unknown VM '{vm_id}' or permission denied for user {uid}"
            )
        if vm_conf.is_user_allowed(uid):
            return vm_conf
    if usages is None:
        raise VMProxyException(
            f"Unknown VM '{vm_id}' or permission denied for user {uid}"
        )
    raise VMProxyException(
        f"Unknown VM '{vm_id}' or permission denied for user {uid} to {', '.join([u.value for u in usages])}"
    )


def _check_privileges_on_vm_version(
    gconf: Configuration, vm_id: str, uid: int, vmversion: VMVersion
):
    """Check the user has a right to manipulate a VM version"""
    match vmversion.version_type:
        case VMVersionType.BASE:
            _get_vm_conf_for_usage(gconf, vm_id, uid, [VMUsage.UPDATE])
        case VMVersionType.USER:
            _get_vm_conf_for_usage(gconf, vm_id, uid, [VMUsage.RUN])
        case VMVersionType.SNAP:
            _get_vm_conf_for_usage(gconf, vm_id, uid, [VMUsage.RUN])
        case _:
            raise VMProxyException(
                f"CODEBUG: Unhandled VMVersionType {vmversion.version_type}"
            )  # pyright: ignore


async def vm_publish(
    gconf: Configuration, sys_call: Callable, vm_id: str, uid: int, message: str
) -> str:
    """Actually publish a VM version and returns the new VM version ID"""
    vm_conf = _get_vm_conf_for_usage(gconf, vm_id, uid, [VMUsage.UPDATE])
    result = await sys_call({"cmde": "vm-files-list", "vm-dir": vm_conf.directory})
    if result is None:
        raise VMProxyException("CODEBUG: 'vm-files-list' returned None")
    avmf = AdminVMFiles.deserialize(result)

    vmversion = avmf.get_staged(VMVersionType.BASE, uid)
    if vmversion is not None:
        vmf = avmf.get_vm_files(uid)
        if vmf is None:
            raise VMProxyException(f"CODEBUG: no VMFiles for user {uid}")
        _check_privileges_on_vm_version(gconf, vm_id, uid, vmversion)
        data = await sys_call(
            {
                "cmde": "vm-publish",
                "vmversion-uid": uid,
                "vm-id": vm_id,
                "vm-version": vmversion.id,
                "message": message,
            }
        )
        return str(VMVersion.deserialize(data))
    else:
        raise VMProxyException("No staging version for this VM")


async def vm_merge(
    gconf: Configuration,
    sys_call: Callable,
    vm_id: str,
    zone_name: str | None,
    uid: int,
    vtype: VMVersionType,
    vnum: int | None,
    message: str,
):
    """Actually merge a VM version into its parent"""
    match vtype:
        case VMVersionType.BASE | None:
            vm_conf = _get_vm_conf_for_usage(gconf, vm_id, uid, [VMUsage.UPDATE])
        case VMVersionType.USER:
            vm_conf = _get_vm_conf_for_usage(gconf, vm_id, uid, [VMUsage.RUN])
        case VMVersionType.SNAP:
            vm_conf = _get_vm_conf_for_usage(gconf, vm_id, uid, [VMUsage.RUN])
        case _:
            raise VMProxyException(f"CODEBUG: unknown VMVersionType {vtype}")

    result = await sys_call({"cmde": "vm-files-list", "vm-dir": vm_conf.directory})
    if result is None:
        raise VMProxyException("CODEBUG: 'vm-files-list' returned None")
    avmf = AdminVMFiles.deserialize(result)

    if vnum is None:
        raise VMProxyException("CODEBUG: vnum is None")
    vmversion = avmf.get_vm_version(vtype, vnum, zone_name, uid)
    if vmversion is None:
        raise VMProxyException(f"VM version type {vtype}, num {vnum}, zone {zone_name} for UID {uid} not found")
    if vmversion not in avmf.committable_versions:
        raise VMProxyException("VM version cannot be merged yet")
    _check_privileges_on_vm_version(gconf, vm_id, uid, vmversion)

    vmf = avmf.get_vm_files(uid)
    if vmf is None:
        raise VMProxyException(f"CODEBUG: no VMFiles for user {uid}")
    match vtype:
        case VMVersionType.BASE:
            await sys_call(
                {
                    "cmde": "vm-commit",
                    "vmversion-uid": uid,
                    "vm-id": vm_id,
                    "vm-version": vmversion.id,
                    "message": message,
                }
            )
        case VMVersionType.USER:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None, vmf.commit_version, vmversion, None, None, None, message
            )
        case VMVersionType.SNAP:
            if zone_name is None:
                raise VMProxyException("CODEBUG: zone_name should not be None for snapshot VM version")
            new_user_vmversion = VMVersion(
                VMVersionType.USER,
                vmversion.directory,
                vmf.get_next_user_version_number(zone_name),
            )
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None,
                vmf.commit_version,
                vmversion,
                new_user_vmversion,
                None,
                None,
                message,
            )
            vnum = new_user_vmversion.version_number


async def vm_cleanup(
    gconf: Configuration, sys_call: Callable, vm_id: str, zone_name: str | None = None
) -> list[str]:
    # discard all the obsolete (unused anymore) VM versions
    return await sys_call({"cmde": "vm-cleanup", "vm-id": vm_id, "zone": zone_name})


async def vm_discard(
    gconf: Configuration,
    sys_call: Callable,
    vm_id: str,
    vm_version: str,
    zone_name: str | None,
    force: bool,
) -> list[str]:
    # discard all the obsolete (unused anymore) VM versions
    (uid, vtype, vnum, staged, nickname) = parse_vm_version(vm_version)
    if vtype is None:
        raise VMProxyException("Could not determine VM version type")
    return await sys_call(
        {
            "cmde": "vm-discard-files",
            "zone": zone_name,
            "vmversion-uid": uid,
            "vm-id": vm_id,
            "vtype": vtype.value,
            "vnum": vnum,
            "staged": staged,
            "nickname": nickname,
            "force": force,
        }
    )
