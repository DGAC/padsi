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
# a VMVersion is an object to manage all the actuel files required to run a VM:
# - a QEMU image
# - an OVMF vars file
# - an infos file
#

from __future__ import annotations

import enum
import os
import re
import shutil
import subprocess
import syslog

import psutil

import nsbubble

from .vmdb import VMDB, Event, EventType


def _copy_reflink(src:str, dest:str) :
    """Make a shallow (COW) copy of a file
    """
    if os.path.realpath(src)==os.path.realpath(dest):
        return
    cenv=os.environ.copy()
    cenv["LANG"]="C"
    proc=subprocess.run(["cp", "--reflink==always", src, dest], env=cenv, capture_output=True, text=True)
    if proc.returncode==0:
        return

    if "Operation not supported" in proc.stderr:
        raise Exception(f"Can't COW file '{src}' to '{dest}', operation not supported by the filesystem")
    raise Exception(proc.stderr)

class VMVersionType(str, enum.Enum):
    BASE="base"
    USER="user"
    SNAP="snap"

class VMState(str, enum.Enum):
    CREATED="CREATED"
    RUNNING="RUNNING"
    DISCARDED="DISCARDED"
    STOPPED="STOPPED"

class VMVersion:
    """Represent a VM version with 3 files:
    - an image file (QEMU image file), named "base.[<version>.].img
    - a vars file (UEFI variables) named "base.[<version>.].vars
    - an infos file containing misc. information named "base.[<version>.].infos
    """
    def __init__(self, vtype:VMVersionType, directory:str, version:int|None=None):
        self._type=vtype
        self._directory=directory
        self._version=version
        self._id=f"{vtype.value}:{version}:{directory}" # unique ID
        self._files=["", "", ""] # full path to image, vars and infos files respectively
        if version is None:
            self._files[0]=os.path.join(directory, f"{vtype.value}.img")
            self._files[1]=os.path.join(directory, f"{vtype.value}.vars")
            self._files[2]=os.path.join(directory, f"{vtype.value}.infos")
        else:
            self._files[0]=os.path.join(directory, f"{vtype.value}.{version}.img")
            self._files[1]=os.path.join(directory, f"{vtype.value}.{version}.vars")
            self._files[2]=os.path.join(directory, f"{vtype.value}.{version}.infos")

        self._backing_image_file_name=None
        self._uid:int|None=None # used for __str__() only
        self._staged:bool|None=None # used for __str__() only
        self._zone_name:str|None=None # not used internally, but may be set as an attribute

    def __repr__(self) -> str:
        return f"VMVersion {self._id}"

    def __str__(self) -> str:
        p1="" if self._uid is None else f"{self._uid}/"
        p2="staged" if self._staged is True else self._type.value
        return f"{p1}{p2}" if self._version is None else f"{p1}{p2}.{self._version}"

    @property
    def domain_name(self) -> str:
        """Valid domain name representation"""
        p="staged" if self._staged is True else self._type.value
        return f"{p}" if self._version is None else f"{p}.{self._version}".replace('.', '-')

    def __hash__(self) -> int:
        return hash(self._id)

    def __eq__(self, other):
        if other is None:
            return False
        return self._id==other._id

    @property
    def uid(self) -> int|None:
        return self._uid

    @uid.setter
    def uid(self, uid:int):
        self._uid=uid

    @property
    def staged(self) -> bool|None:
        return self._staged

    @staged.setter
    def staged(self, staged:bool):
        self._staged=staged

    @property
    def zone_name(self) -> str|None:
        """Associated zone name, if (exterbally) defined
        """
        return self._zone_name

    @zone_name.setter
    def zone_name(self, zone_name:str):
        self._zone_name=zone_name

    @classmethod
    def from_files(cls, img_file:str, vars_file:str, infos_file:str) -> VMVersion:
        """Create an object from actual existing files.
        Note: there is no files' contents validation in any way
        """
        def _parse_filename(fname:str) -> tuple[VMVersionType, int|None, str]:
            parts=fname.split(".")
            try:
                vtype=VMVersionType(parts[0])
                version=int(parts[1]) if len(parts)==3 else None
                return (vtype, version, parts[-1])
            except Exception:
                raise Exception(f"Invalid VM version file name '{fname}'")

        _img_file=os.path.realpath(img_file)
        _vars_file=os.path.realpath(vars_file)
        _infos_file=os.path.realpath(infos_file)
        dir=os.path.dirname(_img_file)
        if os.path.dirname(_vars_file)!=dir or os.path.dirname(_infos_file)!=dir:
            raise Exception("All files for the VM version should be in the same directory")

        (vtype, version, ext)=_parse_filename(os.path.basename(_img_file))
        (vvtype, vversion, vext)=_parse_filename(os.path.basename(_vars_file))
        (ivtype, iversion, iext)=_parse_filename(os.path.basename(_infos_file))
        if (vtype, version)!=(vvtype, vversion) or \
           (vtype, version)!=(ivtype, iversion):
           raise Exception("All files for the VM version should respect the same convention")

        if ext!="img" or vext!="vars" or iext!="infos":
            raise Exception("Some files' extension is not correct")

        return cls(vtype, dir, version)

    @property
    def id(self) -> str:
        return self._id

    @property
    def is_complete(self) -> bool:
        """True if all the files for this VM version are present
        """
        for index in range(0, 3):
            if not os.path.isfile(self._files[index]):
                return False
        return True

    @property
    def is_nonexisting(self) -> bool:
        """True of none of the files exist for this VM version
        """
        for index in range(0, 3):
            if os.path.isfile(self._files[index]):
                return False
        return True

    @property
    def version_type(self) -> VMVersionType:
        return self._type

    @property
    def version_number(self) -> int|None:
        return self._version

    @property
    def directory(self) -> str:
        """Directory in which all the files are"""
        return self._directory

    @property
    def image_file(self) -> str:
        """Full path to the VM image file
        Warning: the file may or may not actually exist
        """
        return self._files[0]

    @property
    def backing_image_file(self) -> str|None:
        """Get the backing image file if the VM version is a snapshot of another image file,
        or None otherwise
        """
        if self.image_file is None:
            return None
        if self._backing_image_file_name is None:
            image=nsbubble.QEMUImageFile(self.image_file)
            bimg=image.backing
            self._backing_image_file_name=None if bimg is None else bimg.image_file_name
        return None if self._backing_image_file_name is False else self._backing_image_file_name

    @property
    def vars_file(self) -> str:
        """Full path to the VM VARS file
        Warning: the file may or may not actually exist
        """
        return self._files[1]

    @property
    def infos_file(self) -> str:
        """Full path to the VM infos file
        Warning: the file may or may not actually exist
        """
        return self._files[2]

    @property
    def state(self) -> VMState|None:
        """Get the state of this VM version
        """
        try:
            if os.path.exists(self.infos_file):
                with VMDB(self.infos_file) as db:
                    state=VMState(db.state)
                    if state==VMState.RUNNING and self._update_running_state():
                        state=VMState(db.state)
                    return state
        except Exception as e:
            syslog.syslog(syslog.LOG_WARNING, f"Failed to get the state of VM {self}: {str(e)}")
        return None

    def set_state(self, state:VMState, context:str|None=None):
        """Define the VM's state
        """
        try:
            with VMDB(self.infos_file) as db:
                db.set_state(state.value, context)
        except Exception as e:
            raise Exception(f"Could not change VM version state to '{state}' (context '{context}'): {str(e)}")

    def _update_running_state(self) -> bool:
        """If the VM version's state is defined as running, make sure it's actually still running.
        If we are sure it's not, then mark it as discarded
        Returns True if the state was updated
        """
        if self.version_type not in (VMVersionType.SNAP, VMVersionType.USER) and not self.staged:
            syslog.syslog(syslog.LOG_WARNING, f"VM version '{self}' is marked as RUNNING but is not a user customization, a snapshot or staged")

        # check we are in the 'init' PID namespace, otherwise we can't see all the QEMU processes
        # there is no definitive way to do that, so, we consider ourselves in the 'init' PID if
        # we can't read /proc/1/exe's link (we are a normal user) or /proc/1/exe points to systemd (we are root)
        try:
            path=os.readlink("/proc/1/exe")
            if "systemd" not in path:
                return False
        except Exception:
            pass

        # get the QEMU's PID
        qemu_pid=self._raw_get_qemu_pid()
        if qemu_pid is None:
            self.set_state(VMState.DISCARDED, "State update (no QEMU running instance found)")
            return True
        return False

    def _raw_get_qemu_pid(self) -> int|None:
        """Try to identify the QEMU process running the VM version (in the PID namespace of the caller)
        Returns None if none was found
        We don't care about the actual reported state here because it's used to actually update the state
        """
        if not self.is_complete:
            return None
        for fname in os.listdir("/proc"):
            try:
                pid=int(fname)
                proc=psutil.Process(pid)
                if "qemu" in proc.exe():
                    for openedfile in proc.open_files():
                        if openedfile.path==self.image_file:
                            return pid
            except Exception:
                pass
        return None

    def get_qemu_pid(self) -> int|None:
        """Try to identify the QEMU process running the VM version (in the PID namespace of the caller)
        Returns None if none was found
        """
        return self._raw_get_qemu_pid() if self.state==VMState.RUNNING else None

    @property
    def nickname(self) -> str|None:
        """Get the nickname of this VM version, or None if none defined
        """
        if os.path.exists(self.infos_file):
            with VMDB(self.infos_file) as db:
                return db.nickname
        return None

    @nickname.setter
    def nickname(self, nickname:str):
        if self.version_type!=VMVersionType.SNAP:
            raise Exception(f"{self.version_type.value} VM versions don't have nicknames")
        try:
            if not self.__class__.nickname_is_valid(nickname):
                raise Exception(f"Invalid VM version's nickname '{nickname}'")
            with VMDB(self.infos_file) as db:
                db.nickname=nickname
        except Exception as e:
            raise Exception(f"Could not change VM version nickname to '{nickname}': {str(e)}")

    def discard_files(self):
        """Remove all the files, if any"""
        for fname in self._files:
            try:
                os.remove(fname)
            except FileNotFoundError:
                pass

    def initialize_files(self, size_mb:int, secure_boot:bool=True, dest_uid:int|None=None, dest_gid:int|None=None):
        """Create initial default files.
        If this VM version is complete, an exception is raised, otherwise any existing file is first removed
        """
        if self.is_complete:
            raise Exception("A VM version already exists")
        self.discard_files()

        try:
            os.makedirs(os.path.dirname(self.image_file), exist_ok=True)
            (_, ovmf_vars)=nsbubble.BubbleVM.get_ovmf_files(secure_boot)
            shutil.copyfile(ovmf_vars, self.vars_file)
            nsbubble.QEMUImageFile.create(self.image_file, size_mb=size_mb)
            with VMDB(self.infos_file): # force creation of infos DB file
                pass
            duid=os.geteuid()
            dgid=os.getegid()
            to_chown=False
            if dest_uid is not None and duid!=dest_uid:
                duid=dest_uid
                to_chown=True
            if dest_gid is not None and dgid!=dest_gid:
                dgid=dest_gid
                to_chown=True
            if to_chown:
                for fname in self._files:
                    os.chown(fname, duid, dgid)

        except Exception as e:
            for fname in self._files:
                try:
                    os.remove(fname)
                except Exception:
                    pass
            raise e

    def import_files(self, hdd_file:str, vars_file:str, message:str|None=None):
        """Copy the specified files, and initialize a new infos. file
        """
        if self.is_complete:
            if self.staged:
                raise Exception("A staged VM version already exists")
            raise Exception(f"VM version {str(self)} already exists")
        try:
            os.makedirs(os.path.dirname(self.image_file), exist_ok=True)
            shutil.copyfile(hdd_file, self.image_file)
            shutil.copyfile(vars_file, self.vars_file)
            with VMDB(self.infos_file) as db:
                ts=db.add_event(EventType.INFORMATIONAL, f"Imported by user {os.getuid()} from '{hdd_file}' and '{vars_file}'")
                if message:
                    db.add_event(EventType.INFORMATIONAL, message, forced_ts=ts)
                db.set_state(VMState.STOPPED.value, "Imported external VM files")
        except Exception as e:
            self.discard_files()
            raise e

    def copy(self, dest:VMVersion, dest_uid:int|None=None, dest_gid:int|None=None):
        """Copy (or reflink if possible) this VM version
        If the target VM version is complete, an exception is raised, otherwise any existing file is first removed
        If a destination user is specified, the new files will be owned by that user
        """
        if dest_uid is None and dest_gid is not None or \
            dest_uid is not None and dest_gid is None:
            raise Exception("Destination UID and GID must either both be specified or not at all")
        if not self.is_complete:
            raise Exception("VM version is not complete")
        if self.image_file==dest.image_file:
            raise Exception("Destination VM version is identical to the one to derive")
        if dest.is_complete:
            raise Exception("Destination VM version files must be discarded first")
        dest.discard_files()

        try:
            # try to do some COW
            cloned=False
            try:
                for i, fname in enumerate(self._files):
                    _copy_reflink(fname, dest._files[i])
                cloned=True
            except Exception:
                pass

            # fall back
            if not cloned:
                for i, fname in enumerate(self._files):
                    shutil.copy(fname, dest._files[i])

            if dest_uid is not None and dest_gid is not None:
                for fname in dest._files:
                    os.chown(fname, dest_uid, dest_gid)

            with VMDB(dest.infos_file) as db:
                db.add_event(EventType.INFORMATIONAL, f"Copied from '{self}'")

        except Exception as e:
            for fname in dest._files:
                try:
                    os.remove(fname)
                except Exception:
                    pass
            raise e

    def _check_manipulations_arguments(self, dest:VMVersion, dest_uid:int|None=None, dest_gid:int|None=None):
        if dest_uid is None and dest_gid is not None or \
            dest_uid is not None and dest_gid is None:
            raise Exception("Destination UID and GID must either both be specified or not at all")
        if not self.is_complete:
            raise Exception("VM version is not complete")
        if self.image_file==dest.image_file:
            raise Exception("Destination VM version is identical to the source one")
        if dest.is_complete:
            raise Exception("Destination VM version files must be discarded first")

    def derive(self, dest:VMVersion, dest_uid:int|None=None, dest_gid:int|None=None):
        """Derive this VM version (create a snapshot of the image file)
        If the target VM version is complete, an exception is raised, otherwise any existing file is first removed
        If a destination user is specified, the new files will be owned by that user
        """
        self._check_manipulations_arguments(dest, dest_uid, dest_gid)
        dest.discard_files()

        try:
            qimage=nsbubble.QEMUImageFile(self.image_file)
            qimage.create_snapshot(dest.image_file)
            shutil.copyfile(self.vars_file, dest.vars_file)
            shutil.copyfile(self.infos_file, dest.infos_file)
            if dest_uid is not None and dest_gid is not None:
                for fname in dest._files:
                    os.chown(fname, dest_uid, dest_gid)

            with VMDB(dest.infos_file) as db:
                db.add_event(EventType.INFORMATIONAL, f"Derived from '{self}'")

        except Exception as e:
            for fname in dest._files:
                try:
                    os.remove(fname)
                except Exception:
                    pass
            raise e

    def move(self, dest:VMVersion, dest_uid:int|None=None, dest_gid:int|None=None, message:str|None=None):
        """Move the files of this VM version to another VM version
        """
        self._check_manipulations_arguments(dest, dest_uid, dest_gid)
        dest.discard_files()

        index=0
        try:
            shutil.move(self.image_file, dest.image_file)
            index+=1
            shutil.move(self.vars_file, dest.vars_file)
            index+=1
            shutil.move(self.infos_file, dest.infos_file)

            if dest_uid is not None and dest_gid is not None:
                for fname in dest._files:
                    os.chown(fname, dest_uid, dest_gid)

            with VMDB(dest.infos_file) as db:
                db.add_event(EventType.INFORMATIONAL, message if message else f"Moved from '{self}'")
        except Exception as e:
            for i in range(0, index):
                try:
                    shutil.move(dest._files[index], self._files[index])
                except Exception:
                    pass
            raise e

    def commit(self, backing:VMVersion, dest:VMVersion|None, dest_uid:int|None=None, dest_gid:int|None=None, message:str|None=None):
        """'commit' the current VM version to the specified VM version (the changes in this VM version will be committed to that new backing file)

        Notes:
          - the VM version must have a backing file and the backing file MUST NOT be used
            by another snapshot, otherwise that snapshot will be rendered useless
          - if dest is not None, then the VM version's files will not exist anymore after the operation
            specifically:
            - the VARS and INFOS files will be moved to the dest VM version
            - the backing file will be renamed to dest.image_file (the original backing file won't exist anymore)
        """
        image=nsbubble.QEMUImageFile(self.image_file)
        backing_bimage=image.backing
        if backing_bimage is None:
            raise Exception("VM version's image file does not have any backing file (not a QEMU snapshot)")

        if backing.image_file!=backing_bimage.image_file_name:
            raise Exception(f"Specified backing VM version {backing} has backing image file '{backing.image_file}' which is incoherent with the backing QEMU image file '{backing_bimage.image_file_name}'")

        if dest is None:
            # actual commit
            image.commit()

            # backing file renaming
            try:
                os.rename(backing_bimage.image_file_name, self.image_file)
            except Exception as e:
                raise Exception(f"Can't rename backing file '{backing_bimage.image_file_name}' to '{self.image_file}': {str(e)}")

            with VMDB(self.infos_file) as db:
                if message:
                    evmsg=f"{message} (committed to backing image file '{backing_bimage.image_file_name}')"
                else:
                    evmsg=f"Committed to backing image file '{backing_bimage.image_file_name}'"
                ts=db.add_event(EventType.INFORMATIONAL, evmsg)
        else:
            self._check_manipulations_arguments(dest, dest_uid, dest_gid)
            if self.version_type==VMVersionType.BASE:
                if dest.version_type!=VMVersionType.BASE:
                    raise Exception(f"VM version types mismatch for commit: {self.version_type.value} / {dest.version_type.value}")
            dest.discard_files()

            # move the VARS and INFOS files
            shutil.move(self.vars_file, dest.vars_file)
            try:
                shutil.move(self.infos_file, dest.infos_file)
            except Exception as e:
                try:
                    shutil.move(dest.vars_file, self.vars_file)
                except Exception:
                    pass
                raise e
            with VMDB(dest.infos_file) as db:
                db.add_event(EventType.INFORMATIONAL, f"Moved from '{self}'")

            # backing file renaming
            try:
                os.rename(backing_bimage.image_file_name, dest.image_file)
            except Exception as e:
                raise Exception(f"Can't rename backing file '{backing_bimage.image_file_name}' to '{dest.image_file}': {str(e)}")

            # change the backing file name and do the commit
            backing_bimage=image.rename_backing_file(dest.image_file)
            image.commit()

            with VMDB(dest.infos_file) as db:
                ts=db.add_event(EventType.INFORMATIONAL, f"Committed from '{backing}'")
                if message:
                    db.add_event(EventType.INFORMATIONAL, message, forced_ts=ts)

        self._backing_image_file_name=None
        backing.discard_files() # cleanup backing version as its QEMU image file has just been move

    def change_backing_version(self, new_backing:VMVersion):
        """Force the modification of the QEMU backing file
        """
        if not self.is_complete:
            raise Exception("VM version is not complete")
        if self.image_file==new_backing.image_file:
            raise Exception("Destination VM version is identical to the source one")

        image=nsbubble.QEMUImageFile(self.image_file)
        if image.backing_image_file_name is None:
            raise Exception("VM version has no backing VM version")
        image.rename_backing_file(new_backing.image_file)
        self._backing_image_file_name=None

    def derives_from(self, other:VMVersion) -> bool:
        """Tell if this VM version derives from another VM version
        """
        if not self.is_complete or not other.is_complete:
            raise Exception("VM version is not complete")

        if self.image_file==other.image_file:
            return False

        image=nsbubble.QEMUImageFile(self.image_file)
        return other.image_file in image.get_backing_files_names()

    def add_history_event(self, evtype:EventType, descr:str|None=None):
        """Add an event which will show in the history
        """
        if os.path.exists(self.infos_file):
            with VMDB(self.infos_file) as db:
                db.add_event(evtype=evtype, descr=descr)
        else:
            raise Exception(f"VM version '{self}' does not have any associated infos file")

    def get_history(self, parent:VMVersion|None=None) -> list[Event]:
        """Get the history of the VM version (limiting it to what's not in its parent
        if a parent VM version is specified)
        """
        evtypes=[EventType.INFORMATIONAL, EventType.VM_CREATED, EventType.VM_STARTED, EventType.VM_SHUTDOWN, EventType.VM_DISCARDED]
        last_event:Event|None=None
        if parent is not None:
            try:
                if os.path.exists(self.infos_file):
                    with VMDB(parent.infos_file) as db:
                        events=db.get_events(evtypes, count_limit=1)
                        if len(events)>0:
                            last_event=events[0]
            except Exception as e:
                syslog.syslog(syslog.LOG_ERR, f"Could not get history of VM version {parent}: {str(e)}")
        events:list[Event]=[]
        if os.path.exists(self.infos_file):
            with VMDB(self.infos_file) as db:
                events=db.get_events(evtypes)
                if last_event is not None:
                    found=False
                    nevents=[]
                    for event in events:
                        if event==last_event:
                            found=True
                            break
                        nevents.append(event)
                    events=nevents
                    if not found:
                        syslog.syslog(syslog.LOG_WARNING, f"Problem in history of VM version {self} with parent {parent}: parent last event '{last_event}' not found")
        return events

    @classmethod
    def nickname_is_valid(cls, nickname:str) -> bool:
        """Tell is a VM version's nickname is valid:

        Note: must not be "staged" as it is reserved for staged VM versions
        """
        if re.match(r"^[a-zA-Z]+[a-zA-Z0-9_-]*$", nickname):
            return nickname not in ("staged", "base", "user", "snapshot")
        return False

    def serialize(self) -> dict:
        return {
            "type": self._type.value,
            "dir": self._directory,
            "version": self._version,
            "files": self._files,
            "backing": self._backing_image_file_name,
            "uid": self._uid,
            "staged": self._staged
        }

    @classmethod
    def deserialize(cls, data:dict) -> VMVersion:
        obj=cls(VMVersionType(data["type"]), data["dir"], data["version"])
        obj._files=data["files"]
        obj._backing_image_file_name=data["backing"]
        obj.uid=data["uid"]
        obj._staged=data["staged"]
        return obj

def parse_vm_version(vm_version:str) -> tuple[int|None, VMVersionType, int|None, bool, str|None]:
    """Parse a string representing a VM version
    format:
      [<user ID>/]<base|user|snapshot>.[version]
      [<user ID>/]staged
      [<user ID>/]<nickname>

    Returns: user ID, version type, version number, staged?, nickname
    """
    try:
        # with user ID?
        parts=vm_version.split("/")
        if len(parts)==2:
            userid=int(parts[0])
            remain=parts[1]
        elif len(parts)!=1:
            raise Exception()
        else:
            userid=None
            remain=vm_version

        # other part
        parts=remain.split(".")
        if len(parts) not in (1,2):
            raise Exception()
        num=None
        nickname=None
        if parts[0]=="staged":
            vtype=VMVersionType.BASE
        else:
            try:
                vtype=VMVersionType(parts[0])
                if len(parts)>1:
                    num=int(parts[1])
                    if num<0:
                        raise Exception()
            except Exception:
                vtype=VMVersionType.SNAP
                nickname=remain
                if not VMVersion.nickname_is_valid(nickname):
                    raise Exception()

        return (userid, vtype, num, num is None and nickname is None, nickname)
    except Exception:
        raise Exception("invalid VM version format")
