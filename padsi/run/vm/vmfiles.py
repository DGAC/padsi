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
# Handling the VM artefacts of VMs defined in the configuration
#

from __future__ import annotations

import hashlib
import os
import pwd
import syslog
from dataclasses import dataclass
from typing import Any

from .version import VMState, VMVersion, VMVersionType
from .vmdb import Event


@dataclass
class VMVersionInfo:
    """Information about a VM version in the context of a VMFiles object"""
    dependencies:str|None
    parent:VMVersion|None
    children:list[VMVersion]

class VMFiles:
    """Object to manage all the files associated to a specific VM (declared in the configuration)

    This object manages a directory structure below the vm_dir directory specified when the object is created, as follows:
    - files: base VM image files (zero, one or more VM versions)
    - staging/<UID>: per user directory to store staged VM files (staging is where VM are created and updated before being "published"
      to be usable by the users)
    - zones/<zone name>/<UID>: per zone and per user directory to store both user-customized VM versions (type VMVersionType.USER) and
      user executed VM versions (type VMVersionType.SNAP)
    """
    def __init__(self, vm_dir:str, uid:int|None=None, gid:int|None=None, analyse:bool=True):
        if not os.path.isabs(vm_dir):
            raise Exception(f"Expected vm_dir '{vm_dir}' to be an absolute directory")
        self._vm_dir=vm_dir
        self._uid=uid if uid is not None else os.geteuid()
        try:
            self._gid=gid if gid is not None else pwd.getpwuid(self._uid).pw_gid
        except KeyError:
            # in case the user does not exist (anymore)
            self._gid=self._uid

        self._all_versions_by_id:dict[str, VMVersion]={} # key=VMVersion.id, value=VMVersion, serves to keep existing objects while calling _analyse()
        self._all_versions_by_image:dict[str, VMVersion]={} # key=VMVersion.image_file, value=VMVersion
        self._reverse_dependencies:dict[VMVersion, set[VMVersion]] # for each VM version, list the VM versions which are directlry derived from it

        self._staged_versions:dict[VMVersionType,VMVersion]={} # stating VM versions (at most one per VMVersionType)
        self._base_versions:dict[int, VMVersion]={} # list of all the base (VMVersionType.BASE) VM versions, indexed by version number
        self._user_versions:dict[int, set[VMVersion]]={} # list of all the user customized (VMVersionType.USER) VM versions, indexed by version number
        self._snap_versions:dict[int, set[VMVersion]]={} # list of all the user's snapshot (VMVersionType.SNAP) VM versions, indexed by version number
        self._obsolete_versions:list[VMVersion]=[] # list of all the VM versions which can safely be removed
        self._committable_versions:list[VMVersion]=[] # list of all the VM versions which can safely be committed (as they have a backing VM version)

        self._unused_files:list[str]=[] # list of files which are not used

        if analyse:
            self._analyse()

    @property
    def uid(self) -> int:
        return self._uid

    @property
    def gid(self) -> int:
        return self._gid

    #
    # directories' names
    #
    def create_common_dirs(self):
        """Create directories common to all users.
        This should be done while running as root
        """
        for dname in ("staging", "zones"):
            os.makedirs(os.path.join(self._vm_dir, dname), exist_ok=True)

    @property
    def directory(self) -> str:
        """Get the "top" directory from where the object operates
        """
        return self._vm_dir

    @property
    def staging_directory(self) -> str:
        """Get the directory which can be used to install or update VMs before copying the actual
        files to their final destination if everything went right.
        """
        return os.path.join(self._vm_dir, "staging", str(self._uid))

    def get_zone_directory(self, zone_name:str) -> str:
        """Get the directory in which all the user specific resources for a specified zone will reside
        """
        return os.path.join(self._vm_dir, "zones", zone_name, str(self._uid))

    def get_vm_version(self, version_type:VMVersionType, version:int, zone_name:str|None) -> VMVersion|None:
        match version_type:
            case VMVersionType.BASE:
                return self.get_base_version(version)
            case VMVersionType.USER:
                if zone_name is None:
                    raise Exception("no zone specified (user VM versions are bound to zones)")
                return self.get_user_version(version, zone_name)
            case VMVersionType.SNAP:
                if zone_name is None:
                    raise Exception("no zone specified (snapshot VM versions are bound to zones)")
                return self.get_snapshot_version(version, zone_name)
            case _:
                raise Exception(f"CODEBUG: unknown VMVersionType {version_type}") # pyright: ignore

    def get_vm_version_by_id(self, vmversion_id:str) -> VMVersion|None:
        return self._all_versions_by_id.get(vmversion_id)

    #
    # manage staged versions
    #
    def get_staged(self, vtype:VMVersionType) -> VMVersion|None:
        """Tell if there is a staging version
        """
        return self._staged_versions.get(vtype)

    def is_staged(self, vmversion:VMVersion) -> bool:
        return vmversion==self._staged_versions.get(vmversion.version_type)


    #
    # manage base versions
    #
    @property
    def base_versions(self) -> list[VMVersion]:
        """Get all the base versions available
        """
        res=list(self._base_versions.values())
        res.sort(key=lambda x: x.version_number if x.version_number is not None else 0)
        return res

    def get_base_version(self, version:int) -> VMVersion|None:
        return self._base_versions.get(version)

    def is_base(self, vmversion:VMVersion) -> bool:
        if vmversion.version_number is None:
            return False
        return vmversion==self.get_base_version(vmversion.version_number)

    @property
    def last_base_version(self) -> VMVersion|None:
        """Get the last existing version of the base VM image files
        or None if the base VM image files don't yet exist
        """
        if len(self._base_versions)>0:
            v=max(self._base_versions)
            return self._base_versions[v]
        return None

    @property
    def next_base_version_number(self) -> int:
        vmversion=self.last_base_version
        return 0 if vmversion is None or vmversion.version_number is None else vmversion.version_number+1


    #
    # manage user versions
    #
    def get_all_user_versions(self, zone_name:str|None=None) -> list[VMVersion]:
        """Get all the VM versions customized for the user, optionally for the specified zone
        """
        res=[]
        for vmv_set in self._user_versions.values():
            if zone_name is None:
                res+=vmv_set
            else:
                res+=[vm_version for vm_version in vmv_set if vm_version.zone_name==zone_name]
        res.sort(key=lambda x: x.version_number if x.version_number is not None else 0)
        return res

    def get_user_version(self, version:int, zone_name:str) -> VMVersion|None:
        """Get a specific user version"""
        for vmversion in self._user_versions.get(version, []):
            if vmversion.zone_name==zone_name:
                return vmversion
        return None

    def is_user(self, vmversion:VMVersion) -> bool:
        if vmversion.version_number is None or vmversion.zone_name is None:
            return False
        return vmversion==self.get_user_version(vmversion.version_number, vmversion.zone_name)

    def get_last_user_version(self, zone_name:str) -> VMVersion|None:
        """Get the last existing version of the user VM image files
        or None if the user VM image files don't yet exist
        """
        if len(self._user_versions)>0:
            v=max(self._user_versions)
            for vmversion in self._user_versions[v]:
                if vmversion.zone_name==zone_name:
                    return vmversion
        return None

    def last_user_version_is_obsolete(self, zone_name:str) -> bool|None:
        """Tell if the last user version, if it exists, is based on the laset available base version
        of not.
        Returns:
        - None if there is no last user version
        - True if it's based on the last available base version
        - False otherwise
        """
        vmv=self.get_last_user_version(zone_name)
        if vmv is None:
            return None
        base_vmv=self.last_base_version
        if base_vmv is None:
            # code bug somewhere
            return None
        return not vmv.derives_from(base_vmv)

    def get_next_user_version_number(self, zone_name:str) -> int:
        vmversion=self.get_last_user_version(zone_name)
        return 0 if vmversion is None or vmversion.version_number is None else vmversion.version_number+1

    def get_derived_user_versions(self, vmversion:VMVersion) -> list[VMVersion]:
        """Get the VM versions of type USER which are derived from a specific base version
        """
        res=[]
        for vmv in self.get_all_user_versions():
            if vmv.derives_from(vmversion):
                res.append(vmv)
        return res


    #
    # manage snapshot versions
    #
    def get_all_snapshot_versions(self, zone_name:str|None=None) -> list[VMVersion]:
        """Get all the snapshot VM versions, optionally for the specified zone
        """
        res=[]
        for vmv_list in self._snap_versions.values():
            if zone_name is None:
                res+=vmv_list
            else:
                res+=[vm_version for vm_version in vmv_list if vm_version.zone_name==zone_name]
        res.sort(key=lambda x: x.version_number if x.version_number is not None else 0)
        return res

    def get_snapshot_version(self, version:int, zone_name:str) -> VMVersion|None:
        """Get a specific user version"""
        for vmversion in self._snap_versions.get(version, []):
            if vmversion.zone_name==zone_name:
                return vmversion
        return None

    def is_snapshot(self, vmversion:VMVersion) -> bool:
        if vmversion.version_number is None or vmversion.zone_name is None:
            return False
        return vmversion==self.get_snapshot_version(vmversion.version_number, vmversion.zone_name)

    def get_last_snapshot_version(self, zone_name:str) -> VMVersion|None:
        """Get the last existing version of the snapshot VM image files
        or None if there is none
        """
        if len(self._snap_versions)>0:
            v=max(self._snap_versions)
            for vmversion in self._snap_versions[v]:
                if vmversion.zone_name==zone_name:
                    return vmversion
        return None

    def get_next_snapshot_version_number(self, zone_name:str) -> int:
        vmversion=self.get_last_snapshot_version(zone_name)
        return 0 if vmversion is None or vmversion.version_number is None else vmversion.version_number+1

    def get_named_snapshot_version(self, zone_name:str, nickname:str) -> VMVersion|None:
        """Get a VM version from its VM version or its nickname
        If more than one VM version have the same nickname (which should not happen under normal
        circumnstances), then an exception is raised
        """
        vmv:VMVersion|None=None
        for vmvlist in self._snap_versions.values():
            for vmversion in vmvlist:
                if vmversion.nickname==nickname and vmversion.zone_name==zone_name:
                    if vmv is None:
                        vmv=vmversion
                    else:
                        raise Exception(f"VM versions '{str(vmv)}' and '{str(vmversion)}' have the same nickname")
        return vmv

    #
    # versions' creation
    #
    def stage_from_scratch(self, vtype:VMVersionType, size_mb:int, secure_boot:bool=True, dest_uid:int|None=None, dest_gid:int|None=None) -> VMVersion:
        """Create new VM version in the staging area for install and update operations
        If a CREATED or DISCARDED version already exists, then it is first removed, otherwise an exception is raised if a version
        already exists
        """
        vmversion=VMVersion(vtype, self.staging_directory)
        if vmversion.state in (VMState.CREATED, VMState.DISCARDED):
            vmversion.discard_files()
        vmversion.initialize_files(size_mb, secure_boot, dest_uid, dest_gid)
        self._all_versions_by_id[vmversion.id]=vmversion
        self._analyse()
        return vmversion

    def stage_imported_files(self, hdd_file:str, vars_file:str, message:str|None=None) -> VMVersion:
        """Create a new staged VM version by making a copy of some existing VM files
        """
        vmversion=VMVersion(VMVersionType.BASE, self.staging_directory)
        vmversion.staged=True
        if vmversion.state in (VMState.CREATED, VMState.DISCARDED):
            vmversion.discard_files()
        vmversion.import_files(hdd_file, vars_file, message)
        self._all_versions_by_id[vmversion.id]=vmversion
        self._analyse()
        return vmversion

    def stage_existing_version(self, vmversion:VMVersion, dest_uid:int|None=None, dest_gid:int|None=None) -> VMVersion:
        """Create a new staged VM version from an existing VM version
        If a non DISCARDED version already exists, then an exception is raised
        """
        self._check_version_directory(vmversion)
        if vmversion.version_number is None:
            raise Exception("VM version is already a staged VM version")

        if not vmversion.is_complete or vmversion.state!=VMState.STOPPED:
            raise Exception(f"VM version {vmversion.id} is not complete or in the finished state ({vmversion.state})")

        target=VMVersion(vmversion.version_type, self.staging_directory)
        target=self._all_versions_by_id.get(target.id, target)
        if target.is_complete and target.state==VMState.STOPPED:
            raise Exception("There is already a staged VM version")
        if target.state==VMState.DISCARDED:
            target.discard_files()

        vmversion.derive(target, dest_uid, dest_gid)
        self._all_versions_by_id[target.id]=target
        self._analyse()
        return target

    def publish_staged(self, vmversion:VMVersion, dest_uid:int|None=None, dest_gid:int|None=None, message:str|None=None) -> VMVersion:
        """Make a staged VM version available to the user"""
        self._check_version_directory(vmversion)
        if vmversion.version_number is not None:
            raise Exception("VM version is not a staged VM version")

        if not vmversion.is_complete or vmversion.state!=VMState.STOPPED:
            raise Exception(f"VM version {vmversion.id} is not complete or in the finished state")

        if vmversion.version_type==VMVersionType.BASE:
            nv=self.next_base_version_number
            target=VMVersion(VMVersionType.BASE, self._vm_dir, nv)
            target=self._all_versions_by_id.get(target.id, target)
            vmversion.move(target, dest_uid, dest_gid, message=message)

            self._all_versions_by_id[target.id]=target
            self._analyse()
            return target

        raise Exception("Only BASE versions can be published")

    def create_user_version(self, zone_name:str, base_vmversion:VMVersion|None=None) -> VMVersion:
        """Create a VM version which will be customized for the user
        """
        vmversion=base_vmversion
        if vmversion is None:
            vmversion=self.last_base_version
            if vmversion is None:
                raise Exception("No base VM version exists yet")
        else:
            self._check_version_directory(vmversion)

        zonedir=self.get_zone_directory(zone_name)
        os.makedirs(zonedir, exist_ok=True)

        target=VMVersion(VMVersionType.USER, zonedir, self.get_next_user_version_number(zone_name))
        target.zone_name=zone_name
        target=self._all_versions_by_id.get(target.id, target)
        vmversion.derive(target, os.geteuid(), os.getegid())
        self._all_versions_by_id[target.id]=target
        self._analyse()
        return target

    def create_snaphot_version(self, zone_name:str, user_vmversion:VMVersion|None=None) -> VMVersion:
        """Create a VM version which will actually be run by the user
        """
        vmversion=user_vmversion
        if vmversion is None:
            vmversion=self.get_last_user_version(zone_name)
            if vmversion is None:
                raise Exception("No user VM version exists yet")
        else:
            self._check_version_directory(vmversion)

        zonedir=self.get_zone_directory(zone_name)
        os.makedirs(zonedir, exist_ok=True)

        target=VMVersion(VMVersionType.SNAP, zonedir, self.get_next_snapshot_version_number(zone_name))
        target.zone_name=zone_name
        target=self._all_versions_by_id.get(target.id, target)
        vmversion.derive(target, os.geteuid(), os.getegid())
        self._all_versions_by_id[target.id]=target
        self._analyse()
        return target

    def commit_version(self, vmversion:VMVersion, to_version:VMVersion|None=None, dest_uid:int|None=None, dest_gid:int|None=None, message:str|None=None):
        """Commit a VM version: merge all the changes with the VM version wich has its backing QEMU image file
        and creates the specified VM version as a result.

        Note:
        - if to_version is not None, then this will result in a new VM version, otherwise, the changes are in-place
        - compared to directly calling VMVersion.commit(), this function also updates the backing QEMU files of
          VM versions depending on the VM version to commit.
        """
        self._check_version_directory(vmversion)
        vmv=self._all_versions_by_id.get(vmversion.id, vmversion)
        if vmv not in self._committable_versions:
            raise Exception("VM version is not committable")

        if vmversion.backing_image_file is None:
            raise Exception(f"CODEBUG: VM version {vmversion} is committable but has not backend image file")
        backing_version=self._all_versions_by_image.get(vmversion.backing_image_file)
        if backing_version is None:
            raise Exception(f"CODEBUG: committable VM version {vmversion} has no backing VM version")

        # actually commit
        vmversion.commit(backing_version, to_version, dest_uid, dest_gid, message=message)

        if to_version is not None:
            # update any VM version which depend on the committed VM version
            for dvmv in self.get_children_versions(vmversion):
                dvmv.change_backing_version(to_version)

        del self._all_versions_by_id[backing_version.id]
        self._analyse()

    #
    # Analysis of the files present
    #
    def _analyse(self):
        # note: all the objects in the self._all_versions_by_id dict are kept and not replaced by any other similar object

        past_versions=self._all_versions_by_id
        def _replace_or_self(vmversion:VMVersion, new_versions:dict[str, VMVersion]) -> VMVersion:
            # ensure we don't end up with VMVersion duplicate objects for the same actual files
            vmv=past_versions.get(vmversion.id)
            if vmv is not None:
                return vmv
            vmv=new_versions.get(vmversion.id)
            if vmv is not None:
                return vmv
            return vmversion

        all_used_files:list[str]=[]
        self._all_versions_by_image={}

        self._staged_versions={}
        self._base_versions={}
        self._user_versions={}
        self._snap_versions={}
        self._obsolete_versions=[]
        self._committable_versions=[]
        self._unused_files=[]
        self._all_versions_by_id={}
        self._reverse_dependencies={}
        all_zones:set[str]=set()

        # staging files
        for vtype in VMVersionType:
            vmversion=None
            try:
                vmversion=VMVersion(vtype, self.staging_directory)
                backing_image=vmversion.backing_image_file # make sure there is no error with this regards
                if vmversion.is_complete:
                    vmversion=_replace_or_self(vmversion, self._all_versions_by_id)
                    self._staged_versions[vtype]=vmversion
                    vmversion.staged=True
                    self._all_versions_by_id[vmversion.id]=vmversion
                    all_used_files.append(vmversion.image_file)
                    all_used_files.append(vmversion.vars_file)
                    all_used_files.append(vmversion.infos_file)
                    self._all_versions_by_image[vmversion.image_file]=vmversion
            except Exception as e:
                if vmversion is not None and vmversion.is_complete:
                    syslog.syslog(syslog.LOG_WARNING, f"VMVersion {vmversion} has problems, ignoring it: {str(e)}")

        # list VM versions
        for fname in os.listdir(self._vm_dir):
            fpath=os.path.join(self._vm_dir, fname)

            if os.path.isfile(fpath):
                parts=fname.split(".")
                try:
                    if parts[0]==VMVersionType.BASE.value:
                        vmversion=None
                        try:
                            vmversion=VMVersion(VMVersionType.BASE, self._vm_dir, int(parts[1]))
                            backing_image=vmversion.backing_image_file # make sure there is no error with this regards
                            if vmversion.is_complete:
                                vmversion=_replace_or_self(vmversion, self._all_versions_by_id)
                                assert(vmversion.version_number is not None)
                                self._base_versions[vmversion.version_number]=vmversion
                                #print(f"+base {vmversion.version_number}: {vmversion.id}")
                                self._all_versions_by_id[vmversion.id]=vmversion
                                all_used_files.append(vmversion.image_file)
                                all_used_files.append(vmversion.vars_file)
                                all_used_files.append(vmversion.infos_file)
                                self._all_versions_by_image[vmversion.image_file]=vmversion
                        except Exception as e:
                            if vmversion is not None and vmversion.is_complete:
                                syslog.syslog(syslog.LOG_WARNING, f"VMVersion {vmversion} has problems, ignoring it: {str(e)}")
                except Exception:
                    # ignore that file
                    pass
            else:
                # directory
                if fname=="zones":
                    for zone_name in os.listdir(fpath):
                        all_zones.add(zone_name)
                        zone_dir=self.get_zone_directory(zone_name)
                        if os.path.exists(zone_dir):
                            for sfname in os.listdir(zone_dir):
                                parts=sfname.split(".")
                                try:
                                    vtype=VMVersionType(parts[0])
                                    if vtype in (VMVersionType.USER, VMVersionType.SNAP):
                                        vmversion=None
                                        try:
                                            vmversion=VMVersion(vtype, zone_dir, int(parts[1]))
                                            vmversion.zone_name=zone_name
                                            backing_image=vmversion.backing_image_file # make sure there is no error with this regards
                                            if vmversion.is_complete:
                                                vmversion=_replace_or_self(vmversion, self._all_versions_by_id)
                                                assert(vmversion.version_number is not None)
                                                if vtype==VMVersionType.USER:
                                                    if vmversion.version_number in self._user_versions:
                                                        self._user_versions[vmversion.version_number].add(vmversion)
                                                    else:
                                                        self._user_versions[vmversion.version_number]={vmversion}
                                                    #print(f"+user {vmversion.version_number}: {vmversion.id}")
                                                else:
                                                    if vmversion.version_number in self._snap_versions:
                                                        self._snap_versions[vmversion.version_number].add(vmversion)
                                                    else:
                                                        self._snap_versions[vmversion.version_number]={vmversion}
                                                    #print(f"+snap {vmversion.version_number}: {vmversion.id}")
                                                self._all_versions_by_id[vmversion.id]=vmversion
                                                all_used_files.append(vmversion.image_file)
                                                all_used_files.append(vmversion.vars_file)
                                                all_used_files.append(vmversion.infos_file)
                                                self._all_versions_by_image[vmversion.image_file]=vmversion
                                        except Exception as e:
                                            if vmversion is not None and vmversion.is_complete:
                                                syslog.syslog(syslog.LOG_WARNING, f"VMVersion {vmversion} has problems, ignoring it: {str(e)}")
                                except Exception:
                                    # ignore that file
                                    pass

        # identify unused files
        for (root, dirs, files) in os.walk(self._vm_dir):
            # ignore files not for self._uid in staging/ and zones/
            try:
                parts=root.split("/")
                ruid=int(parts[-1])
                if ruid!=self._uid:
                    continue
            except Exception:
                pass

            for fname in files:
                fpath=os.path.join(root, fname)
                if fpath not in all_used_files:
                    self._unused_files.append(fpath)

        # reverse dependencies
        for vmv in self._all_versions_by_id.values():
            backing_image=vmv.backing_image_file
            if backing_image is not None:
                bvmv=self._all_versions_by_image.get(backing_image)
                if bvmv is None:
                    raise Exception(f"CODEBUG: no backing version for VM version {vmv} with backing file '{backing_image}'")
                else:
                    if bvmv not in self._reverse_dependencies:
                        self._reverse_dependencies[bvmv]=set()
                    self._reverse_dependencies[bvmv].add(vmv)

        # obsolete versions which:
        # for BASE and USER versions:
        # - are currently not used AND
        # - have a later available version
        # for SNAP versions: are marked as DISCARDED
        last_base_version=self.last_base_version
        if last_base_version is not None:
            assert(last_base_version.version_number is not None)

        last_user_versions:dict[str, VMVersion]={} # indexed by zone name
        for zone_name in all_zones:
            vmv=self.get_last_user_version(zone_name)
            if vmv is not None:
                last_user_versions[zone_name]=vmv
                assert(vmv.version_number is not None)

        for vmv in self._all_versions_by_id.values():
            if vmv.version_type==VMVersionType.BASE and vmv.version_number is not None and \
                last_base_version is not None and last_base_version.version_number and \
                vmv.version_number<last_base_version.version_number and len(self.get_children_versions(vmv))==0:
                    self._obsolete_versions.append(vmv)
            if vmv.version_type==VMVersionType.USER and vmv.version_number is not None and \
                vmv.version_number is not None and vmv.zone_name is not None:
                luv=last_user_versions.get(vmv.zone_name)
                if luv is not None and luv.version_number is not None and \
                vmv.version_number<luv.version_number and len(self.get_children_versions(vmv))==0:
                    self._obsolete_versions.append(vmv)
            if vmv.version_type==VMVersionType.SNAP and vmv.state==VMState.DISCARDED:
                self._obsolete_versions.append(vmv)

        # committable versions:
        # - versions which have a backing image
        # - the backing VM version only has one VM version depending on it
        for vmv in self._all_versions_by_id.values():
            if vmv.version_type==VMVersionType.BASE and vmv.version_number is not None:
                backing_image=vmv.backing_image_file
                if backing_image is not None:
                    bvmv=self._all_versions_by_image.get(backing_image)
                    if bvmv is None:
                        raise Exception(f"CODEBUG: no backing version for committable VM version {vmv}")
                    else:
                        if len(self.get_children_versions(bvmv))==1:
                            self._committable_versions.append(vmv)

    def refresh(self):
        """Re-analyse the files in the VM's directory
        """
        self._analyse()

    #
    # Misc.
    #
    def get_history(self, vmversion: VMVersion) -> list[Event]:
        """Get the history of a VM version"""
        # TODO: use the parent VM version to check for inconsistencies
        return vmversion.get_history()

    def _check_version_directory(self, vmversion: VMVersion):
        """Ensure that the VM version is in the directory managed by this object
        """
        if vmversion.directory!=self.directory and not vmversion.directory.startswith(self.directory+"/"):
            raise Exception(f"VM version is not in the '{self.directory}' directory")

    def declare_version_object(self, vmversion: VMVersion):
        """Make sure we use the specified vmversion when this object
        will re-analyse the contents of the top directory
        """
        self._check_version_directory(vmversion)
        ev=self._all_versions_by_id.get(vmversion.id)
        if ev is not None and ev is not vmversion:
            raise Exception(f"There is already an object for this VM version '{vmversion.id}'")
        self._all_versions_by_id[vmversion.id]=vmversion

    def get_children_versions(self, vmversion:VMVersion) -> set[VMVersion]:
        """Get all the VM versions which are directly derived from the
        specified version
        """
        return self._reverse_dependencies.get(vmversion, set())

    def get_parent_version(self, vmversion:VMVersion) -> VMVersion|None:
        """Get the VM version which associated QEMU image file is the backing file
        of the specified VM version
        """
        backing_file=vmversion.backing_image_file
        if backing_file is None:
            return None
        return self._all_versions_by_image.get(backing_file)

    def get_vm_version_for_image(self, image_file:str) -> VMVersion|None:
        """Get the VM version which relies on the specified VM image file
        """
        return self._all_versions_by_image.get(image_file)

    def get_vm_version_matching_image(self, image_file:str) -> VMVersion|None:
        """Get the VM version which relies on a VM image file bit for bit equal to the
        specified VM image file
        """
        image_size=os.stat(image_file).st_size
        image_hash:str|None=None
        for (img_file, vm_version) in self._all_versions_by_image.items():
            if os.stat(img_file).st_size==image_size:
                if image_hash is None:
                    with open(image_file, "rb") as fd:
                        image_hash=hashlib.file_digest(fd, "sha256").hexdigest()
                with open(img_file, "rb") as fd:
                    if image_hash==hashlib.file_digest(fd, "sha256").hexdigest():
                        return vm_version
        return None

    @property
    def all_versions(self) -> list[VMVersion]:
        """Get a list of all the VM versions"""
        return list(self._all_versions_by_image.values())

    @property
    def obsolete_versions(self) -> list[VMVersion]:
        """List all obsolete VMVersion objects
        """
        return self._obsolete_versions

    @property
    def committable_versions(self) -> list[VMVersion]:
        """List all (BASE) VM Version objects which can be committed
        Note: the staged VM version, if any, will not be listed here as it's supposed to be published
              and not committed
        """
        return self._committable_versions

    @property
    def unused_files(self) -> list[str]:
        """Get the list of all the files which are unexpected, and can safely be removed.
        Files from obsolete VM versions are not part of this list
        """
        return self._unused_files

    def get_version_info(self, vmversion:VMVersion) -> VMVersionInfo:
        """Get a information about a VM version as a tuple with
        """
        parent_vmversion=self.get_parent_version(vmversion)
        children_descr=None
        children_vmversions=self.get_children_versions(vmversion)
        if len(children_vmversions)>0:
            sub=[str(v) for v in children_vmversions]
            children_descr=f"referenced by {', '.join(sub)}"

        if parent_vmversion is None:
            depinfos=None if children_descr is None else children_descr
        else:
            if children_descr is None:
                depinfos=f"references {str(parent_vmversion)}"
            else:
                depinfos=f"references {str(parent_vmversion)}, {children_descr}"
        return VMVersionInfo(depinfos, parent_vmversion, list(children_vmversions))

    def serialize(self) -> dict[str,Any]:
        return {
            "uid": self._uid,
            "gid": self._gid,
            "vm-dir": self._vm_dir,
            "all-versions": {
                id: vmv.serialize() for (id, vmv) in self._all_versions_by_id.items()
            },
            "by-images": {
                img: vmv.id for (img, vmv) in self._all_versions_by_image.items()
            },
            "rev-deps": {
                kvmv.id: [vmv.id for vmv in vmvset] for (kvmv, vmvset) in self._reverse_dependencies.items()
            },
            "staged-versions": {
                vtype.value: vmv.id for (vtype, vmv) in self._staged_versions.items()
            },
            "base-versions": {
                vnum: vmv.id for (vnum, vmv) in self._base_versions.items()
            },
            "user-versions": {
                vnum: [vmv.id for vmv in vmvlist] for (vnum, vmvlist) in self._user_versions.items()
            },
            "snap-versions": {
                vnum: [vmv.id for vmv in vmvlist] for (vnum, vmvlist) in self._snap_versions.items()
            },
            "committable-versions": [vmv.id for vmv in self._committable_versions],
            "obsolete-versions": [vmv.id for vmv in self._obsolete_versions],
            "unused-files": self._unused_files
        }

    @classmethod
    def deserialize(cls, data:dict[str,Any]) -> VMFiles:
        obj=cls(data["vm-dir"], data["uid"], data["gid"], analyse=False)

        obj._all_versions_by_id={id:VMVersion.deserialize(ser) for (id, ser) in data["all-versions"].items()}
        obj._all_versions_by_image={img:obj._all_versions_by_id[id] for (img, id) in data["by-images"].items()}
        obj._reverse_dependencies={obj._all_versions_by_id[key]: {obj._all_versions_by_id[vmvid] for vmvid in vlist} for (key, vlist) in data["rev-deps"].items()}
        obj._staged_versions={VMVersionType(vtype):obj._all_versions_by_id[id] for (vtype, id) in data["staged-versions"].items()}
        obj._base_versions={int(vnum):obj._all_versions_by_id[id] for (vnum, id) in data["base-versions"].items()}
        obj._user_versions={int(vnum):{obj._all_versions_by_id[id] for id in idslist if obj._all_versions_by_id[id].version_number==int(vnum)} for (vnum, idslist) in data["user-versions"].items()}
        obj._snap_versions={int(vnum):{obj._all_versions_by_id[id] for id in idslist if obj._all_versions_by_id[id].version_number==int(vnum)} for (vnum, idslist) in data["snap-versions"].items()}
        obj._obsolete_versions=[obj._all_versions_by_id[id] for id in data["obsolete-versions"]]
        obj._committable_versions=[obj._all_versions_by_id[id] for id in data["committable-versions"]]
        obj._unused_files=data["unused-files"]

        return obj
