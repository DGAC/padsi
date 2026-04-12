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
# Handling the VM artefacts of VMs defined in the configuration for all users
#

from __future__ import annotations

import os
from typing import Any

from .version import VMVersion, VMVersionType
from .vmfiles import VMFiles, VMVersionInfo


class AdminVMFiles:
    def __init__(self, vm_dir:str, uid:int, analyse:bool=True):
        """Create a global view of all the VM versions in a VM's directory, from the default point of view of the user which UID
        is passed as argument (this user does not have any otherwise specific privilege).
        """
        if not os.path.isabs(vm_dir):
            raise Exception(f"Expected vm_dir '{vm_dir}' to be an absolute directory")
        self._vm_dir=vm_dir
        if not isinstance(uid, int) or uid<0:
            raise Exception(f"Invalid UID '{uid}'")
        self._uid=uid

        self._users:set[int]=set()
        self._vmfs:dict[int,VMFiles]={} # indexec by UID
        self._all_versions:set[VMVersion]=set()
        self._all_versions_by_backing_image:dict[str,set[VMVersion]]={} # key=backing image file

        self._committable_versions:set[VMVersion]=set()
        self._obsolete_versions:set[VMVersion]=set()
        self._unused_files:set[str]=set()

        if analyse:
            self._analyse()

    def get_staged(self, vtype:VMVersionType, uid:int|None=None) -> VMVersion|None:
        """Tell if there is a staging version for the specified user (or this object's default user if user is not specified)
        """
        vmf=self._vmfs.get(self._uid if uid is None else uid)
        return vmf.get_staged(vtype) if vmf is not None else None

    @property
    def users(self) -> set[int]:
        return self._users

    def get_vm_files(self, uid:int|None=None) -> VMFiles|None:
        return self._vmfs.get(self._uid if uid is None else uid)

    def get_vm_version(self, version_type:VMVersionType, version:int, zone_name:str|None, uid:int|None=None) -> VMVersion|None:
        match version_type:
            case VMVersionType.BASE:
                return self.get_base_version(version)
            case VMVersionType.USER:
                if zone_name is None:
                    raise Exception("no zone specified (user VM versions are bound to zones)")
                return self.get_user_version(version, zone_name, uid)
            case VMVersionType.SNAP:
                if zone_name is None:
                    raise Exception("no zone specified (snapshot VM versions are bound to zones)")
                return self.get_snapshot_version(version, zone_name, uid)
            case _:
                raise Exception(f"CODEBUG: unknown VMVersionType {version_type}") # pyright: ignore

    @property
    def base_versions(self) -> list[VMVersion]:
        res:set[VMVersion]=set()
        for (_, vmf) in self._vmfs.items():
            for vmversion in vmf.base_versions:
                res.add(vmversion)
        return list(res)

    def get_base_version(self, version:int) -> VMVersion|None:
        vmf=self._vmfs.get(self._uid)
        return vmf.get_base_version(version) if vmf is not None else None

    def user_versions(self, uid:int|None=None) -> list[VMVersion]:
        vmf=self._vmfs.get(self._uid if uid is None else uid)
        return vmf.get_all_user_versions() if vmf is not None else []

    def get_user_version(self, version:int, zone_name:str, uid:int|None=None) -> VMVersion|None:
        vmf=self._vmfs.get(self._uid if uid is None else uid)
        return vmf.get_user_version(version, zone_name) if vmf is not None else None

    def snapshot_versions(self, uid:int|None=None) -> list[VMVersion]:
        vmf=self._vmfs.get(self._uid if uid is None else uid)
        return vmf.get_all_snapshot_versions() if vmf is not None else []

    def get_snapshot_version(self, version:int, zone_name:str, uid:int|None=None) -> VMVersion|None:
        vmf=self._vmfs.get(self._uid if uid is None else uid)
        return vmf.get_snapshot_version(version, zone_name) if vmf is not None else None

    def get_named_snapshot_version(self, zone_name:str, nickname:str, uid:int|None=None) -> VMVersion|None:
        vmf=self._vmfs.get(self._uid if uid is None else uid)
        return vmf.get_named_snapshot_version(zone_name, nickname) if vmf is not None else None


    @property
    def all_versions(self) -> list[VMVersion]:
        """Get a list of all the VM versions"""
        return list(self._all_versions)

    @property
    def obsolete_versions(self) -> list[VMVersion]:
        """List all obsolete VMVersion objects
        """
        return list(self._obsolete_versions)

    @property
    def committable_versions(self) -> list[VMVersion]:
        """List all (BASE) VM Version objects which can be committed
        Note: the staged VM version, if any, will not be listed here as it's supposed to be published
              and not committed
        """
        return list(self._committable_versions)

    @property
    def unused_files(self) -> list[str]:
        """Get the list of all the files which are unexpected, and can safely be removed.
        Files from obsolete VM versions are not part of this list
        """
        return list(self._unused_files)

    def get_children_versions(self, vmversion:VMVersion) -> set[VMVersion]:
        """Get all the VM versions which are directly derived from the
        specified version
        """
        res=set()
        for vmf in self._vmfs.values():
            res=res.union(vmf.get_children_versions(vmversion))
        return res

    def get_parent_version(self, vmversion:VMVersion) -> VMVersion|None:
        """Get the parent VM version of a specific VM version.
        Returns None if not found
        """
        vmf=self._vmfs.get(self._uid if vmversion.uid is None else vmversion.uid)
        if vmf is None:
            raise Exception(f"No VM version '{vmversion}' found")

        return vmf.get_parent_version(vmversion)

    def _get_users_with_vm_artefacts(self) -> set[int]:
        """Go through all directories and identify which user has any VM artefact
        """
        if not os.path.exists(self._vm_dir):
            raise Exception(f"VM directory '{self._vm_dir}' does not exist")
        all_users:set[int]=set()
        for fname in os.listdir(self._vm_dir):
            if fname in ("staging", "zones"):
                fpath=os.path.join(self._vm_dir, fname)
                for f2name in os.listdir(fpath):
                    if fname=="staging":
                        # expecting f2name to be an UID
                        try:
                            uid=int(f2name)
                            if uid>=0:
                                all_users.add(uid)
                        except Exception:
                            pass
                    else:
                        # expecting f2name to be zone name
                        f2path=os.path.join(fpath, f2name)
                        for f3name in os.listdir(f2path):
                            # expecting f3name to be an UID
                            try:
                                uid=int(f3name)
                                if uid>=0:
                                    all_users.add(uid)
                            except Exception:
                                pass
        return all_users

    def _analyse(self):
        """Analyse all the files in a VM's directory, for all the users having any artefact file
        in that direcrory
        """
        def _reinit():
            self._users=set()
            self._vmfs={}
            self._all_versions=set()
            self._all_versions_by_backing_image={} # key=backing image file

            self._committable_versions=set()
            self._obsolete_versions=set()
            self._unused_files=set()

        _reinit()
        self._users=self._get_users_with_vm_artefacts()

        try:
            commitable=set()
            for uid in self._users:
                vmf=VMFiles(self._vm_dir, uid)
                self._vmfs[uid]=vmf
                for vmv in vmf.all_versions:
                    self._all_versions.add(vmv)
                    backing_mage=vmv.backing_image_file
                    if backing_mage:
                        if backing_mage not in self._all_versions_by_backing_image:
                            self._all_versions_by_backing_image[backing_mage]=set()
                        self._all_versions_by_backing_image[backing_mage].add(vmv)
                    if uid!=self._uid and (vmv.staged or vmv.version_type!=VMVersionType.BASE):
                        vmv.uid=uid # mark those VM versions as belonging to that "other" user

                commitable=commitable.union(set(vmf.committable_versions))
                self._obsolete_versions=self._obsolete_versions.union(set(vmf.obsolete_versions))
                self._unused_files=self._unused_files.union(set(vmf.unused_files))

            # remove VM versions from the list of commitable VM versions if there is
            # more than 1 VM version using the same backing image file
            for vmv in commitable:
                backing_image=vmv.backing_image_file
                if backing_image is not None:
                    depvmv=self._all_versions_by_backing_image[backing_image]
                    if len(depvmv)==1:
                        self._committable_versions.add(vmv)

            # remove VM versions items in self._obsolete_versions if any other VM version depend on them
            false_obso:set[VMVersion]=set()
            for vmv in self._all_versions:
                backing_image=vmv.backing_image_file
                if backing_image is not None:
                    for ovmv in self._obsolete_versions:
                        if ovmv not in false_obso and ovmv.image_file==backing_image:
                            false_obso.add(ovmv)
            if len(false_obso)>0:
                self._obsolete_versions=self._obsolete_versions.difference(false_obso)

        except Exception as e:
            _reinit()
            raise e

    def get_version_info(self, vmversion:VMVersion) -> VMVersionInfo:
        vmf=None
        for (uid, vmfiles) in self._vmfs.items():
            if vmfiles.get_vm_version_by_id(vmversion.id) is not None:
                vmf=vmfiles
                break
        if vmf is None:
            raise Exception(f"Could not determine the VMFiles containing '{vmversion}'")

        parent_vmversion=vmf.get_parent_version(vmversion)

        children_vmversions:set[VMVersion]=set()
        for (_, vmf) in self._vmfs.items():
            children_vmversions=children_vmversions.union(vmf.get_children_versions(vmversion))

        children_descr=None
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
            "vm-dir": self._vm_dir,
            "users": list(self._users),
            "vmf-objects": {
                uid: vmf.serialize() for (uid, vmf) in self._vmfs.items()
            },
            "all-versions": [vmv.id for vmv in self._all_versions],
            "all-versions-b": {img: [vmv.id for vmv in vmvset] for (img, vmvset) in self._all_versions_by_backing_image.items()},
            "committable-versions": [vmv.id for vmv in self._committable_versions],
            "obsolete-versions": [vmv.id for vmv in self._obsolete_versions],
            "unused-files": list(self._unused_files)
        }

    @classmethod
    def deserialize(cls, data:dict[str,Any]) -> AdminVMFiles:
        def _get_vmversion(vmfs:list[VMFiles], vmvid:str) -> VMVersion:
            for vmf in vmfs:
                vmversion=vmf.get_vm_version_by_id(vmvid)
                if vmversion is not None:
                    return vmversion
            raise Exception(f"VMVersion with id '{vmvid}' not found")
        obj=cls(data["vm-dir"], data["uid"], analyse=False) # pyright: ignore
        obj._users={uid for uid in data["users"]} # pyright: ignore
        obj._vmfs={int(uid): VMFiles.deserialize(ser) for (uid, ser) in data["vmf-objects"].items()} # pyright: ignore
        vmfs_list=list(obj._vmfs.values())
        obj._all_versions={_get_vmversion(vmfs_list, id) for id in data["all-versions"]} # pyright: ignore
        obj._all_versions_by_backing_image={img:{_get_vmversion(vmfs_list, id) for id in vmvlist} for (img, vmvlist) in data["all-versions-b"].items()} # pyright: ignore
        obj._committable_versions={_get_vmversion(vmfs_list, id) for id in data["committable-versions"]} # pyright: ignore
        obj._obsolete_versions={_get_vmversion(vmfs_list, id) for id in data["obsolete-versions"]} # pyright: ignore
        obj._unused_files={fname for fname in data["unused-files"]} # pyright: ignore

        return obj
