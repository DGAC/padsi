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

from __future__ import annotations

import os
import shutil
import subprocess
import syslog

from dataclasses import dataclass

_debug=False

def makedirs_keep_owner(path: str):
    """Create directories if they don't already exist keeping the same owner as the last directory
    which exists in the path.
    Intended to be run as root to avoid having directories belonging to root
    """
    uid:int|None=None
    gid:int|None=None
    cpath="/"
    for part in path.split("/"):
        cpath=os.path.join(cpath, part)
        if os.path.exists(cpath):
            st=os.stat(cpath)
            uid=st.st_uid
            gid=st.st_gid
            if not os.path.isdir(cpath):
                raise Exception(f"Path '{cpath}' is supposed to be a directory by is not")
        else:
            os.mkdir(cpath)
            if uid is not None and gid is not None:
                os.chown(cpath, uid, gid)

def _prefixed_mount_path(mpoint:MountPoint, prefix:str|None) -> str:
    if prefix:
        return os.path.join(prefix, mpoint.mount_path[1:])
    return mpoint.mount_path

def _existing_source_path(mpoint:MountPoint, tmp_dir:str) -> str:
    if not os.path.isabs(mpoint._source_path):
        raise Exception(f"source path {mpoint._source_path} is not absoltue")
    if os.path.exists(mpoint.source_path):
        return mpoint.source_path
    if mpoint._is_dir:
        dir=os.path.join(tmp_dir, "_nmount_", mpoint.source_path[1:])
        os.makedirs(dir, exist_ok=True)
        if _debug:
            syslog.syslog(syslog.LOG_DEBUG, f"Created dir for inexisting mount point '{mpoint.source_path}' => {dir}")
        return dir
    else:
        dir=os.path.join(tmp_dir, "_nmount_", os.path.dirname(mpoint.source_path)[1:])
        os.makedirs(dir, exist_ok=True)
        path=os.path.join(dir, os.path.basename(mpoint.source_path))
        if _debug:
            syslog.syslog(syslog.LOG_DEBUG, f"Created file for inexisting mount point'{mpoint.source_path}' => {path}")
        return path

class MountPoint:
    """Mount point to mount a directory or a file from the "host" (the source_path argument) to a path in a bubble (the mount_path argument)
    source_path may not exist (e.g. in an empty writable directory is needed).
    """
    def __init__(self, source_path:str, mount_path:str, readonly:bool=True, monitored:bool=False, require_abs_mount_path:bool=True) -> None:
        if not source_path:
            raise Exception(f"invalid mount point's source path '{source_path}'")
        self._is_dir:bool|None=None
        if os.path.isabs(source_path):
            self._source_path=os.path.realpath(source_path) # what is mounted, may not yet exist
            self._is_dir=os.path.isdir(self.source_path)
        else:
            self._source_path=source_path

        if not mount_path or (require_abs_mount_path and not os.path.isabs(mount_path)):
            raise Exception(f"invalid mount point's mount path '{mount_path}'")

        self._mount_path=mount_path # where it is mounted
        if self._mount_path[-1]=="/":
            if os.path.exists(self.source_path) and not self._is_dir:
                raise Exception("MountPoint's mount path indicates a directory but source path is not a directory")
            self._is_dir=True
            self._mount_path=self._mount_path[:-1]

        self._readonly=readonly
        self._monitored=monitored

        if self._is_dir:
            self._source_path=self._source_path+"/"
            self._mount_path=self._mount_path+"/"

    def __str__(self) -> str:
        return f"{self.mount_path} <== {self.source_path} ({'RO' if self._readonly else 'RW'}{', MONITORED' if self._monitored else ''})"

    @property
    def source_path(self) -> str:
        """Source path being mointed (full path),
        ends with a '/' if it's a directory
        """
        return self._source_path

    @property
    def mount_path(self) -> str:
        """Target path where the source path is mounted (full path),
        ends with a '/' if it's a directory
        """
        return self._mount_path

    @property
    def read_only(self) -> bool:
        return self._readonly

    @property
    def monitored(self) -> bool:
        return self._monitored

    def is_mounted(self) -> bool:
        """Tell if the mount point is mounted and, if mounted, verifies that the correct source path is mounted
        Note: does not check the read-only status
        """
        proc=subprocess.run(["findmnt", "-n", "-o", "SOURCE", self.mount_path], capture_output=True, text=True)
        if proc.returncode!=0:
            if not proc.stderr:
                return False
            raise Exception(f"Could not run findmnt: {proc.stderr}")
        return True

    def mount(self) -> bool:
        """Mount a MountPoint
        Returns True if the mount point was not already mounted
        """
        if self.is_mounted():
            return False

        if _debug:
            syslog.syslog(syslog.LOG_DEBUG, f"Mounting {self.source_path} on {self.mount_path}")

        if not os.path.isabs(self._source_path):
            raise Exception(f"source path {self._source_path} is not absoltue")
        if not os.path.exists(self.mount_path):
            makedirs_keep_owner(self.mount_path)

        opt="bind,ro" if self.read_only else "bind"
        proc=subprocess.run(["mount", "-o", opt, self.source_path, self.mount_path], capture_output=True, text=True)
        if proc.returncode!=0:
            raise Exception(f"Could not mount '{self.source_path}' on '{self.mount_path}': {proc.stderr}")
        return True

    def umount(self) -> bool:
        """Unmount a mount point
        Returns True if the mount point was effectively mounted
        """
        if not self.is_mounted():
            return False
        if _debug:
            syslog.syslog(syslog.LOG_DEBUG, f"Unmounting {self.mount_path} from {self.source_path}")
        proc=subprocess.run(["umount", self.mount_path], capture_output=True, text=True)
        if proc.returncode!=0:
            raise Exception(f"Could not umount '{self.mount_path}' from '{self.source_path}': {proc.stderr}")
        return True


    @classmethod
    def from_data(cls, source_path:str, info:dict)->MountPoint:
        if not isinstance(info, dict):
            raise Exception(f"Invalid mountpoint info {info}")
        mp=info.get("mount-point")
        ro=info.get("read-only", True)
        monit=info.get("monitored", False)
        if (not isinstance(mp, str) or mp=="") or \
            not isinstance(ro, bool) or \
            monit is not None and not isinstance(monit, bool):
            raise Exception(f"Invalid mountpoint info {info}: expected a dict")
        return cls(source_path, mp, ro, False if monit is None else monit)


@dataclass
class MountPointGroup:
    """Group mount points beneath a common top directory"""
    mount_points: list[MountPoint] # ordered
    mount_path:str

    def __str__(self) -> str:
        return f"{self.mount_path} ⊂ [{', '.join([str(mpoint) for mpoint in self.mount_points])}]"

    def __post_init__(self):
        if len(self.mount_points)==0:
            raise Exception("CODEBUG: MountPointGroup has no MountPoint")

    def add(self, mpoint:MountPoint):
        if self.mount_path.startswith(mpoint.mount_path):
            self.mount_path=mpoint.mount_path
            self.mount_points=[mpoint]+self.mount_points
        else:
            self.mount_points.append(mpoint)

    def get_bwrap_args(self, run_dir:str, tmp_dir:str) -> list[str]:
        """Get the arguments which must be passed to BubbleWrap to start a bubble
        - run_dir: directory where the mount points will actually be (prefixing each MountPoint.mount_path)
        - tmp_dir: TMP directory where some writable directories may be created if necessary
        """
        if len(self.mount_points)==0:
            return []

        args:list[str]=[]
        if len(self.mount_points)==1:
            mpoint=self.mount_points[0]
            esp=_existing_source_path(mpoint, run_dir)
            if mpoint.read_only:
                args+=["--ro-bind", esp, mpoint.mount_path]
            elif os.access(esp, os.W_OK):
                # directly bind file if we have write permission
                args+=["--bind", esp, mpoint.mount_path]
            elif mpoint._is_dir:
                # add an overlay to allow write to directory
                ovl_dir=_prefixed_mount_path(mpoint, run_dir)
                if _debug:
                    syslog.syslog(syslog.LOG_DEBUG, f"MAKEDIR for RW {ovl_dir}")
                os.makedirs(ovl_dir)
                args+=[
                    "--overlay-src", esp,
                    "--overlay-src", ovl_dir,
                    "--tmp-overlay", mpoint.mount_path
                ]
            else:
                raise Exception(f"Can't allow RW acces to file '{mpoint.source_path} (as {esp})' which is read-only (use a directory instead)")
        else:
            # use the 1st mpoint as the base of the overlay
            f_mpoint=self.mount_points[0]
            args+=["--overlay-src", _existing_source_path(f_mpoint, run_dir)]

            ref_dir=_prefixed_mount_path(f_mpoint, run_dir) # writable layer
            if _debug:
                syslog.syslog(syslog.LOG_DEBUG, f"MAKEDIR REF_DIR {ref_dir}")
            os.makedirs(ref_dir)
            args+=["--overlay-src", ref_dir]

            # copy the contents of all the other mount points (using bind mount would be better but would require root privs.)
            for mpoint in self.mount_points[1:]:
                if not mpoint.mount_path.startswith(self.mount_path):
                    raise Exception(f"CODEBUG: mount point {mpoint.mount_path} not a sub dir. of group's {self.mount_path}")
                esp=_existing_source_path(mpoint, run_dir)

                delta_path=mpoint.mount_path[len(self.mount_path):]
                if delta_path[0]=="/":
                    delta_path=delta_path[1:]
                dest_path=os.path.join(ref_dir, delta_path)
                os.makedirs(os.path.dirname(dest_path), exist_ok=True)
                if os.path.isdir(esp):
                    if os.path.exists(dest_path):
                        if _debug:
                            syslog.syslog(syslog.LOG_DEBUG, f"COPY recurs. contents of {mpoint.source_path} (as {esp}) to {dest_path}")
                        for fname in os.listdir(esp):
                            fpath=os.path.join(esp, fname)
                            if os.path.isdir(fpath):
                                if _debug:
                                    syslog.syslog(syslog.LOG_DEBUG, f"COPY tree {fpath} to {dest_path}/{fname}")
                                shutil.copytree(fpath, os.path.join(dest_path, fname))
                            else:
                                if _debug:
                                    syslog.syslog(syslog.LOG_DEBUG, f"COPY file {fpath} to {dest_path}/{fname}")
                                shutil.copy2(fpath, os.path.join(dest_path, fname), follow_symlinks=False)
                    else:
                        if _debug:
                            syslog.syslog(syslog.LOG_DEBUG, f"COPY tree {mpoint.source_path} (as {esp}) to {dest_path}")
                        shutil.copytree(esp, dest_path)
                elif os.path.exists(esp):
                    if _debug:
                        syslog.syslog(syslog.LOG_DEBUG, f"COPY file {mpoint.source_path} (as {esp}) to {dest_path}")
                    shutil.copy2(esp, dest_path, follow_symlinks=False)
                else:
                    syslog.syslog(syslog.LOG_ERR, f"Can't copy mount point {mpoint.source_path} (as {esp}) to '{dest_path}': does not exist")

            if f_mpoint.read_only:
                rw_dir=_prefixed_mount_path(f_mpoint, tmp_dir)+"_._rw" # overlay's RW layer (with RO permissions)
                if _debug:
                    syslog.syslog(syslog.LOG_DEBUG, f"MAKEDIR OVL RW_DIR {rw_dir}")
                os.makedirs(rw_dir)
                os.chmod(rw_dir, 0o555)
                work_dir=_prefixed_mount_path(f_mpoint, tmp_dir)+"_._wo" # working layer
                if _debug:
                    syslog.syslog(syslog.LOG_DEBUG, f"MAKEDIR OVL WORK_DIR {work_dir}")
                os.makedirs(work_dir)
                args+=["--overlay", rw_dir, work_dir, self.mount_path]
            else:
                args+=["--tmp-overlay", self.mount_path]
        return args

    def file_source_path(self, filename:str, to_write:bool, run_dir:str) -> str|None:
        """Get the actual (source) path of a file specified in the mount path

        If to_write is False, then the file is actually checked for existance, and None is returned if it does not exist.
        If to_write is True, then the returned path may be in the run_dir directory and pointing to a maybe not yet existing file
        """
        if not filename.startswith(self.mount_path):
            return None

        # take the mount point which has the bigger common path with the filename
        best_mp=None
        for mpoint in self.mount_points:
            if filename.startswith(mpoint.mount_path):
                if best_mp is None:
                    best_mp=mpoint
                else:
                    if len(mpoint.mount_path)>len(best_mp.mount_path):
                        best_mp=mpoint
        assert(best_mp is not None)

        # using the best MountPoint, get the actual path
        suf=filename[len(best_mp.mount_path):]
        spath_s=os.path.join(best_mp.source_path, suf)
        spath_d=os.path.join(_prefixed_mount_path(best_mp, run_dir), suf)

        if to_write:
            return spath_d

        if os.path.exists(spath_d):
            return spath_d
        if os.path.exists(spath_s):
            return spath_s
        return None

class MountPointSet:
    def __init__(self, groups:list[MountPointGroup], run_dir:str):
        self._groups=groups
        self._run_dir=run_dir

    @property
    def groups(self) -> list[MountPointGroup]:
        return self._groups

    def file_source_path(self, filename:str, to_write:bool) -> str|None:
        """Get the actual (source) path of a file specified in the mount path
        If to_write is False, then the file is actually checked for existance, and None is returned if it does not exist.
        If write_to is True, then the returned file name may be in the run directory specified when the object was created and may not yet exist
        """
        for mpgroup in self._groups:
            if _debug:
                syslog.syslog(syslog.LOG_DEBUG, f"file_source_path({mpgroup=}, {filename=}, {to_write=}, {self._run_dir=}) -> {mpgroup.file_source_path(filename, to_write, self._run_dir)}")
            res=mpgroup.file_source_path(filename, to_write, self._run_dir)
            if res:
                return res
        return None

    @classmethod
    def from_specifications(cls, mounts:dict[str,dict]|None, bound_dirs:list[str], run_dir:str) -> MountPointSet:
        """Create a MountPointSet from some specifications"""
        # prepare list of directories which will either be RO-mounted AS-IS, or will be the base of an
        # overlay if we have mount points beneath them.
        # The bound_dirs argument specifies RO dirs which are bound AS-IS from the host suystem
        groups:dict[str,MountPointGroup]={} # key=mount point
        for item in bound_dirs:
            mpoint=MountPoint(item, item)
            mpgrp=MountPointGroup([mpoint], mpoint.mount_path)
            groups[mpgrp.mount_path]=mpgrp

        if mounts is not None:
            # compute MountPoint objects, to be removed when improved API
            mpoints:list[MountPoint]=[]
            for hpath, info in mounts.items():
                mpoints.append(MountPoint.from_data(hpath, info))

            # group mount points in overlays
            for mpoint in mpoints:
                if _debug:
                    syslog.syslog(syslog.LOG_DEBUG, f"handling mountpoint={str(mpoint)}" )
                found=False
                for path in list(groups.keys()).copy():
                    mpgrp=groups[path]
                    if mpoint.mount_path==mpgrp.mount_path:
                        pass
                    elif mpoint.mount_path.startswith(mpgrp.mount_path): # mpoint is a sub directory or mpgrp
                        mpgrp.add(mpoint)
                        found=True
                        break
                    elif mpgrp.mount_path.startswith(mpoint.mount_path): # mpoint is a parent directory of grp
                        ngrp=MountPointGroup([mpoint]+mpgrp.mount_points, mpoint.mount_path)
                        groups[ngrp.mount_path]=ngrp
                        del groups[mpgrp.mount_path]
                        found=True
                        break
                if not found:
                    # new group
                    mpgrp=MountPointGroup([mpoint], mpoint.mount_path)
                    groups[mpgrp.mount_path]=mpgrp

            # merge groups which have the same mount path
            top_paths:set[str]=set()
            for path in sorted(groups.keys()):
                handled=False
                for edir in top_paths.copy():
                    if edir.startswith(path):
                        top_paths.remove(edir)
                        top_paths.add(path)
                        handled=True
                        break
                    elif path.startswith(edir):
                        handled=True
                        break
                if not handled:
                    top_paths.add(path)

            ngroups:dict[str,MountPointGroup]={}
            for path in top_paths:
                ngroups[path]=groups[path]

            for (path, mpgrp) in groups.items():
                if path not in top_paths:
                    # merge with existing group
                    tdir=os.path.dirname(path)
                    egrp=None
                    while tdir!="/":
                        try:
                            egrp=ngroups[tdir+"/"]
                            break
                        except KeyError:
                            tdir=os.path.dirname(tdir)
                    if egrp is None:
                        raise Exception(f"CODEBUG: none of directory '{path}' parents are present in the ngroups")
                    egrp.mount_points+=mpgrp.mount_points
            groups=ngroups

            if _debug:
                for mpgrp in groups.values():
                    syslog.syslog(syslog.LOG_DEBUG, f"MountpointGroup={str(mpgrp)}")

        return cls(list(groups.values()), run_dir)
