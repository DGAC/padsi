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
import datetime
import inspect
import os
import pwd
import re
import subprocess
import syslog
import tempfile
from collections.abc import Callable
from dataclasses import dataclass

import psutil


def get_user_name(uid) -> str:
    """Returns the user's name associated to an UID"""
    return pwd.getpwuid(uid).pw_name

def get_user_home_dir(uid) -> str:
    """Returns the user's home directory associated to an UID (without a last '/')"""
    return pwd.getpwuid(uid).pw_dir

def exec_sync(args:list[str]) -> tuple[int, str, str]:
    sub=subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        (out, err)=sub.communicate(timeout=2)
        retcode=sub.returncode
    except subprocess.TimeoutExpired:
        sub.kill()
        (out, err)=sub.communicate(timeout=2)
        retcode=250

    sout=re.sub(r'[\r\n]+$', '', out.decode()) if out else ""
    serr=re.sub(r'[\r\n]+$', '', err.decode()) if err else ""
    return (retcode, sout, serr)

def get_mnt_namespace(pid: int) -> str:
    """Get the mount namespace of a process.
    """
    try:
        return os.readlink(f"/proc/{pid}/ns/mnt")
    except Exception as e:
        raise Exception(f"Could not get the mount namespace of process with PID {pid}: {str(e)}")

def get_net_namespace(pid: int) -> str:
    """Get the network namespace of a process.
    """
    try:
        return os.readlink(f"/proc/{pid}/ns/net")
    except Exception as e:
        raise Exception(f"Could not get the network namespace of process with PID {pid}: {str(e)}")

def makedirs_with_owner(path:str, set_owner_after:str, uid:int, gid:int):
    """Create directories if they don't already exist with the specified owner
    In the end, the directory specified as 'path' will be created, and along the way, all
    the sub-directories _after_ the one specified as 'set_owner_after' will be owned by the
    specified user.
    """
    set_owner=False
    cpath="/"
    for part in path.split("/"):
        if not set_owner:
            try:
                set_owner=os.path.samefile(cpath, set_owner_after)
            except FileNotFoundError:
                set_owner=False
        cpath=os.path.join(cpath, part)
        if os.path.exists(cpath):
            if not os.path.isdir(cpath):
                raise Exception(f"Path '{cpath}' is supposed to be a directory by is not")
        else:
            os.mkdir(cpath)
            if set_owner:
                os.chown(cpath, uid, gid)

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

xdg_dirs=("DESKTOP", "DOWNLOAD", "TEMPLATES", "PUBLICSHARE", "DOCUMENTS", "MUSIC", "PICTURES", "VIDEOS")

def compute_user_xdg_subdirectories(uid:int) -> dict[str,str]:
    """Compute the user's actual XDG sub-directories _relative to its HOME directory_
    NB: we need to be in the user's environment to execute the xdg-user-dir program in the correct environment because
        this code is executed as root and euid of the user, not in the full user's context
    """
    host_home_dir=get_user_home_dir(uid)
    cenv=os.environ.copy()
    cenv["HOME"]=host_home_dir # must be set in order for the 'xdg-user-dir' program to work
    host_home_dir+="/"

    # (re)create XDG desktop directories (remove the ~/.config/user-dirs.dirs file to force a complete reset)
    # note: xdg-user-dirs-update must be run as the user
    path=os.path.join(host_home_dir, ".config", "user-dirs.dirs")
    if os.path.exists(path):
        os.remove(path)
    if os.geteuid()!=uid:
        if os.geteuid()==0:
            args=["su", "-", pwd.getpwuid(uid).pw_name, "-c", "xdg-user-dirs-update --force"]
        else:
            raise Exception(f"CODEBUG: user '{os.geteuid()}' is trying to reset XDG dirs of user {uid}")
    else:
        args=["xdg-user-dirs-update", "--force"]
    proc=subprocess.run(args, cwd=host_home_dir, capture_output=True, text=True)
    if proc.returncode!=0:
        raise Exception(f"Failed to (re)create XDG directories: {proc.stderr if proc.stderr else proc.stdout}")

    # list directories
    res:dict[str,str]={}
    for xdg_dir in xdg_dirs:
        # xdg_dir will be like "DOCUMENTS"
        proc=subprocess.run(["xdg-user-dir", xdg_dir], env=cenv, capture_output=True, text=True)
        if proc.returncode!=0:
            raise Exception(f"Could not get XDG directory '{xdg_dir}' (unknown to xdg-user-dir?)")
        res_dir=proc.stdout.strip()
        if os.path.samefile(res_dir, host_home_dir):
            syslog.syslog(syslog.LOG_WARNING, f"XDG {xdg_dir}' directory is the same as the user's home directory, ignoring")
        else:
            if not res_dir.startswith(host_home_dir):
                raise Exception(f"XDG {xdg_dir}' directory '{res_dir}' is not in the user's home directory '{host_home_dir}'")
            if not os.path.exists(res_dir):
                # ensure the directory actually exists!
                gid=pwd.getpwuid(uid).pw_gid
                makedirs_with_owner(res_dir, host_home_dir, uid, gid)
            res[xdg_dir]=res_dir[len(host_home_dir):]

    return res

def get_variables_in_string(string):
    """Get the list of variables in the string passed as argument"""
    if not isinstance(string, str):
        raise Exception(f"Expected @string to be a string, got a {type(string)}")
    return re.findall(r'\{!?[a-zA-Z0-9_]{1,}(?:=[^"\'=}]*)?\}', string)

def expand_variables_in_string(string, variables:dict[str,str]|None, extra_variables=None, partial_expand=False) -> str:
    """Modifies the input string to replace any reference to a variable defined in the @variables dictionary.
    The string is left unchanged if @variables is None.

    A variable has the following format: "{" <var name> [=<default value>] "}" where <var name> must only contain a-zA-Z0-9_

    If @partial_expand is False, then any non present variable will result in an exception whereas if it's True,
    the resulted string may contain references to non expanded variabled.

    NB: an exception is raised if a variable referenced in the string cannot be found
    NB: any variable in @extra_variables has priority over variables in @variables
    """
    if variables is None:
        return string
    if not isinstance(variables, dict):
        raise Exception(f"Expected @variables to be a dictionary, got a {type(variables)}")
    if extra_variables is None:
        extra_variables={}
    elif not isinstance(extra_variables, dict):
        raise Exception(f"Expected @extra_variables to be a dictionary, got a {type(extra_variables)}")

    allvars=get_variables_in_string(string)
    for var in allvars:
        rvar=var[1:-1]

        if rvar[0]=="!":
            string=string.replace(var, "{%s}"%rvar[1:])
            continue # ignore this variable

        default=None
        if "=" in rvar:
            (rvar, default)=rvar.split("=")

        if rvar in extra_variables:
            string=string.replace(var, extra_variables[rvar])
        elif rvar in variables:
            if variables[rvar] is None:
                string=string.replace(var, "")
            else:
                string=string.replace(var, str(variables[rvar]))
        elif default is not None:
            string=string.replace(var, default)
        elif not partial_expand:
            raise Exception(f"Can't expand unknown variable '{rvar}'")
    return string

def is_iso_image(iso_file:str) -> bool:
    """Tell if a file is n ISO image"""
    proc=subprocess.run(["file", "-E", "-b", "--mime-type", iso_file], capture_output=True, text=True)
    if proc.returncode!=0:
        raise Exception(f"could not determine if '{iso_file}' is an ISO image: {proc.stdout}")
    return proc.stdout.strip()=="application/x-iso9660-image"

def generate_iso_image(contents:list[str], volume_name:str|None=None, iso_file:str|None=None):
    """Create an ISO image file with the specified contents as a temporary file if iso_file is None
    or directly as the iso_file otherwise
    """
    if len(contents)==0:
        raise Exception("no ISO content specified")
    tmp=None
    if not iso_file:
        tmp=tempfile.NamedTemporaryFile(suffix=".iso")
        iso_file=tmp.name

    args:list[str]=["genisoimage", "-o", iso_file, "-graft-points", "-R", "-J"]
    if volume_name:
        args+=["-V", volume_name]

    aliases:set[str]=set()
    for fname in contents:
        if not isinstance(fname, str):
            raise Exception(f"Invalid ISO content '{fname}'")
        try:
            realname=os.path.realpath(os.path.expanduser(fname))
            if os.path.isfile(realname):
                args+=[realname]
            elif os.path.isdir(realname):
                alias=os.path.basename(realname)
                if alias in aliases:
                    counter=0
                    base_alias=alias
                    while alias in aliases:
                        counter+=1
                        alias=f"{base_alias}_{counter}"
                args+=[f"{alias}={realname}"]
                aliases.add(alias)
            else:
                raise Exception("not a file or directory")
        except Exception as e:
            raise Exception(f"Invalid ISO content '{fname}': {str(e)}")

    proc=subprocess.run(args, capture_output=True, text=True)
    if proc.returncode!=0:
        raise Exception(f"Could not create ISO image file: {proc.stderr if proc.stderr else proc.stdout}")
    return tmp

class LateFunction:
    """This object allows one to define a delay after which a function will be called,
    and in the meantime to either increase the delay after the function will be called or
    to cancel the call altogether
    The callable function can either be an async function or not.
    """
    def __init__(self, callable:Callable, *args):
        self._wait_until:float|None=None
        self._task:asyncio.Task|None=None
        self._callback:Callable=callable
        self._args=args
        self._cancelled=False

    def _wait_done_cb(self, task:asyncio.Task):
        # don't keep a reference to the task
        self._task=None

    def start(self, wait_duration:float):
        """Set or increase the delay after which the function will be called
        """
        if self._wait_until is None:
            now=datetime.datetime.now().timestamp()
            self._wait_until=now+wait_duration
            self._task=asyncio.create_task(self._wait())
            self._task.add_done_callback(self._wait_done_cb)
        else:
            self._wait_until+=wait_duration

    def cancel(self):
        """Cancel the call of the function"""
        if self._task is None:
            return
        self._cancelled=True

    async def _wait(self):
        now=datetime.datetime.now().timestamp()
        if self._wait_until is None:
            raise Exception("CODEBUG: self._wait_until should not be None")
        while True:
            await asyncio.sleep(self._wait_until-now)
            if self._cancelled:
                self._cancelled=False
                return

            now=datetime.datetime.now().timestamp()
            if now>self._wait_until:
                loop=asyncio.get_event_loop()
                self._wait_until=None
                if inspect.iscoroutinefunction(self._callback):
                    await self._callback(self._args)
                else:
                    await loop.run_in_executor(None, self._callback, *self._args)
                return

async def asyncio_run(args:list[str]) -> tuple[int,str,str]: # returncode, stdout, stderr
    """Execute a process and return its output
    """
    proc=await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    (out, err)=await proc.communicate()
    return (proc.returncode, out.decode(), err.decode()) # pyright: ignore

@dataclass
class _UserSession:
    uid:int
    gid:int
    shell_pid:int

class UserSessionNotifier:
    """Watches when users open and close graphical (GNOME shell for now) sessions
    and call a callback when it happens. Needs to be sub-classed
    """
    def __init__(self):
        self._sessions:dict[int,_UserSession]={}

    def user_logged_in_cb(self, uid:int, gid:int, shell_proc:psutil.Process):
        """Function called when a user has logged in
        """
        pass

    def user_logged_out_cb(self, uid:int):
        """Function called when a user has logged out
        """
        pass

    async def run(self):
        p1=psutil.Process(1) # system's init process
        while True:
            try:
                try:
                    # get the list of all GNOME shell processes (each indicating a user with an opened session)
                    all_shell_pids:list[int]=[]
                    for systemd_proc in [p for p in p1.children() if p.name()=="systemd"]:
                        shell_procs=[p for p in systemd_proc.children() if p.name()=="gnome-shell"]
                        if len(shell_procs)>0:
                            if len(shell_procs)>1:
                                syslog.syslog(syslog.LOG_WARNING, "User's systemd has more than one gnome-shell processes!")
                            shell_proc=shell_procs[0]
                            all_shell_pids.append(shell_proc.pid)
                            if shell_proc.pid not in self._sessions:
                                try:
                                    uid=shell_proc.uids().real
                                    if uid>=1000:
                                        # this is a real user
                                        session=_UserSession(uid, shell_proc.gids().real, shell_proc.pid)
                                        self._sessions[session.shell_pid]=session
                                        self.user_logged_in_cb(session.uid, session.gid, shell_proc)
                                except Exception as e:
                                    syslog.syslog(syslog.LOG_ERR, f"Error handling user {shell_proc.uids().real} logged: {str(e)}")

                    # handle users which have logged out
                    for pid in [pid for pid in self._sessions if pid not in all_shell_pids]:
                        try:
                            session=self._sessions.get(pid)
                            if session is not None:
                                self.user_logged_out_cb(session.uid)
                                del self._sessions[pid]

                        except Exception as e:
                            syslog.syslog(syslog.LOG_ERR, f"Error handling user's shell {pid} logged out: {str(e)}")

                except psutil.ZombieProcess:
                    pass # come back later, transient state
                except psutil.AccessDenied:
                    syslog.syslog(syslog.LOG_ERR, "Error handling user logged or unlogged: process list access denied")
                except Exception as e:
                    syslog.syslog(syslog.LOG_ERR, f"Error handling user logged or unlogged: {str(e)}")
            except asyncio.exceptions.IncompleteReadError:
                break

            try:
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                return
