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
# Module to manipulate VM version's save and load archives
#

from __future__ import annotations

import datetime
import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import uuid

from padsi.config import VirtualMachine

from .version import VMVersion
from .vmfiles import VMFiles


class VMArchiveException(Exception):
    pass

def _get_archive_element(tar:tarfile.TarFile, path:str) -> tarfile.TarInfo:
        """Find an element in the tar archive"""
        try:
            ti=tar.getmember(path)
            if not ti.isfile():
                raise VMArchiveException()
            return ti
        except KeyError:
            raise VMArchiveException(f"Invalid archive: missing or invalid file '{path}'")

class VMArchive:
    """Class to manipulate saved and to load VM versions packaged as a single TAR archive
    The archive may be _complete_ (it holds all the necessayr files) or _partial_ (some files are expected
    to already be present in the system when the archive is loaded)
    """
    def __init__(self, ar_file:str):
        self._ar_file=ar_file
        self._when:str|None=None
        self._vm_id:str|None=None
        self._dependency_size:int|None=None
        self._dependency_hash:str|None=None
        self._analyse_manifest()

    @property
    def vm_id(self) -> str|None:
        return self._vm_id

    @vm_id.setter
    def vm_id(self, vm_id:str):
        """Force the VM ID"""
        self._vm_id=vm_id

    @property
    def dependency_size(self) -> int|None:
        return self._dependency_size

    @property
    def dependency_hash(self) -> str|None:
        return self._dependency_hash

    def _analyse_manifest(self):
        with tarfile.open(self._ar_file, "r") as tar:
            # get manifest
            ti=_get_archive_element(tar, "manifest.json")
            try:
                data=tar.extractfile(ti)
                if data is None:
                    raise VMArchiveException()
                manifest=json.load(data)
                self._when=manifest.get("saved-UTC")
                if not isinstance(self._when, str):
                    raise VMArchiveException()
                self._vm_id=manifest.get("vm-id")
                if not isinstance(self._vm_id, str):
                    raise VMArchiveException()
                self._dependency_size=manifest.get("dependency-size")
                self._dependency_hash=manifest.get("dependency-hash")
                if self._dependency_hash is None and self._dependency_size is not None or \
                   self._dependency_hash is not None and self._dependency_size is None:
                   raise VMArchiveException()
                if self._dependency_size is not None and (not isinstance(self._dependency_size, int) or self._dependency_size<=0):
                    raise VMArchiveException()
                if self._dependency_hash is not None and not isinstance(self._dependency_hash, str):
                    raise VMArchiveException()
            except Exception: # noqa: BLE001
                raise VMArchiveException("Could not open archive, or nvalid archive: missing or invalid manifest")

    def extract(self, vm_conf:VirtualMachine, uid:int|None=None) -> str:
        """Extract the VM version's files in the <staged_dir>/<extract-id> directory where extract-id is a generated UUDI4
        If the archive is partial, this function makes a very basic check based on the file size
        before extracting it to the selected directory (the complete hash check will be performed by the user service when asked)

        If a user ID is specified, then the archive will be extracted in its associated directory.

        Return the extract-id
        """
        if uid is not None and os.geteuid() not in (uid, 0):
           raise VMArchiveException(f"Not enough privileges to extract VM's files as user {uid} (being {os.geteuid()})")

        vm_files:VMFiles|None=None
        if self._dependency_size is not None:
            # get partial's VM version image file size if any and find already installed VM version
            # with a matching image size
            vm_files=VMFiles(vm_conf.directory, uid=uid)
            vm_version:VMVersion|None=None
            for vm_v in vm_files.base_versions:
                if os.stat(vm_v.image_file).st_size==self._dependency_size:
                    vm_version=vm_v
                    break
            if vm_version is None:
                raise VMArchiveException("Could not find any base VM version for this partial archive")

        # extract the archive's contents
        if vm_files is None:
            vm_files=VMFiles(vm_conf.directory, uid=uid, analyse=False)
        extract_id=str(uuid.uuid4())
        extract_dir=os.path.join(vm_files.staging_directory, extract_id)
        os.makedirs(extract_dir)
        with tarfile.open(self._ar_file) as tar:
            tar.extractall(extract_dir)

        if uid is not None and os.geteuid() != uid:
            proc=subprocess.run(["chown", "-R", f"{uid}", extract_dir], capture_output=True, text=True, check=False)
            if proc.returncode!=0:
                try:
                    # get rid of files if possible
                    shutil.rmtree(extract_dir)
                except Exception: # noqa: BLE001,S110
                    pass
                raise VMArchiveException(f"Could not change ownership of '{extract_dir}' to user {uid}: {proc.stderr}")
        return extract_id

    @classmethod
    def create(cls, vm_conf:VirtualMachine, vm_version:VMVersion, ar_file:str, depend_vm_version:VMVersion|None) -> VMArchive:
        """Create a new archive for the specified VM version. If depend_vm_version is not None, it must represent
        a VM version which the "remote" PADSI instance which will later load the archive is expected to already have locally;
        this reduces the size of the archive.
        """
        def _add_vm_version_to_archive(ar:tarfile.TarFile, vm_v:VMVersion, index:int):
            dir_path=f"vm-version-{index}"
            path=os.path.join(dir_path, "base.img")
            ar.add(vm_v.image_file, path)
            path=os.path.join(dir_path, "base.vars")
            ar.add(vm_v.vars_file, path)
            path=os.path.join(dir_path, "base.infos")
            ar.add(vm_v.infos_file, path)

        vmf=VMFiles(vm_conf.directory)
        manifest:dict={
            "saved-UTC": datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H:%M:%S"),
            "vm-id": vm_conf.id,
        }

        # build the chain of dependencies
        dependencies:list[VMVersion]=[]
        parent=vmf.get_parent_version(vm_version)
        while parent is not None:
            if not parent.is_complete:
                raise VMArchiveException(f"VM version {parent} is not complete")
            dependencies.append(parent)
            parent=vmf.get_parent_version(parent)

        # create the archive
        with tarfile.open(ar_file, "w") as ar:
            _add_vm_version_to_archive(ar, vm_version, 0)
            index=1
            for dep_vm_v in dependencies:
                if dep_vm_v==depend_vm_version:
                    with open(dep_vm_v.image_file, "rb", buffering=0) as f:
                        manifest["dependency-hash"]=hashlib.file_digest(f, "sha256").hexdigest()
                        manifest["dependency-size"]=os.stat(dep_vm_v.image_file).st_size
                    break
                else:
                    _add_vm_version_to_archive(ar, dep_vm_v, index)
                index+=1

            # add manifest
            print("Adding manifest")
            with tempfile.NamedTemporaryFile("wt") as tmpman:
                tmpman.write(json.dumps(manifest))
                tmpman.flush()
                ar.add(tmpman.name, "manifest.json")

        return cls(ar_file)
