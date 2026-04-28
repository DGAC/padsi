#
# Copyright (c) 2025-2026 DGAC/DSNA
# Copyright (c) 2024 Vivien Malerba <vmalerba@gmail.com>
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

import datetime
import enum
import grp
import ipaddress
import json
import os
import pwd
import shutil
import signal
import socket
import stat
import subprocess
import sys
import syslog
import tempfile
import threading
import time
from dataclasses import dataclass
from urllib import parse

import dbus
import psutil
import pyinotify
import requests
import requests_unixsocket

system_ns_mountdir="/run/netns" # at least for Debian
_debug=False

class BubbleState(str, enum.Enum):
    INITIALIZED = "INITIALIZED"
    RUNNING = "RUNNNING"
    TERMINATED = "TERMINATED"

class ListenEventHandler(pyinotify.ProcessEvent):
    def __init__(self, monitored:list[ShadowedFile], killswitch_path:str):
        self._monitored=monitored
        self._killswitch=killswitch_path
        self.must_stop:bool=False

    def process_IN_CLOSE_WRITE(self, event):
        if event.pathname==self._killswitch:
            self.must_stop=True
        for sfile in self._monitored:
            if sfile.file_path==event.pathname:
                sfile.handle_modified()
                break

def _overwite_file_content(src:str, dest:str):
    """Overwrite and truncate or create the destination file with the
    contents of the src file"""
    if os.path.realpath(src)==os.path.relpath(dest):
        return
    with open(src, "rb") as sfd:
        with open(dest, "wb") as dfd:
            while True:
                data=sfd.read(1024)
                if len(data)>0:
                    dfd.write(data)
                if len(data)<1024:
                    break

class ShadowedFile:
    """Object to map a file to its shadow, which can be
    subclassed in case the "shadowing" is not just a simple copy
    of the file"""
    def __init__(self, file_path:str):
        if not os.path.exists(file_path):
            raise Exception(f"File path '{file_path}' does not exist")
        self._file_path=os.path.realpath(file_path)
        self._shadow=tempfile.NamedTemporaryFile()
        os.chmod(self._shadow.name, os.stat(self._file_path).st_mode)
        if _debug:
            syslog.syslog(syslog.LOG_DEBUG, f"ShadowedFile {self._file_path} --> {self._shadow.name}")

    @property
    def file_path(self) -> str:
        return self._file_path

    @property
    def file_shadow(self) -> str:
        return self._shadow.name

    def handle_modified(self):
        """Make a copy of the monitored file.
        This function can be overrided if necessary
        """
        _overwite_file_content(self._file_path, self._shadow.name)

class FilesMonitor:
    """Class to monitor files which needs to be copied to a file which name can be obtained
    using the file_shadow property
    """
    def __init__(self):
        self._monitored:list[ShadowedFile]=[]
        self._thread:threading.Thread|None=None
        self._killswitch=None
        self._handler:ListenEventHandler|None=None

    def start_monitoring(self):
        """Start a thread to actually monitor all the registered files
        """
        if len(self._monitored)>0 and self._thread is None:
            self._killswitch=tempfile.NamedTemporaryFile()
            self._thread=threading.Thread(target=self._inotify_main)
            self._thread.start()

    def stop_monitoring(self):
        # stop the monitoring thread
        if self._thread is not None:
            if _debug:
                syslog.syslog(syslog.LOG_DEBUG, f"File monitoring stop requested (killswitch is {self._killswitch.name if self._killswitch is not None else '---'})")
            self._killswitch.write(b"END") # pyright: ignore
            self._killswitch.flush() # pyright: ignore
            self._killswitch.close() # pyright: ignore
            self._thread.join() # wait to the thread to terminate
            if _debug:
                syslog.syslog(syslog.LOG_DEBUG, "File monitoring stopped")

            self._killswitch=None
            self._thread=None

    def _check_must_stop(self, notifier) -> bool:
        # will return True if the notifier's loop must be stopped (called after each event)
        return self._handler.must_stop if self._handler is not None else False

    def _inotify_main(self):
        """Function executed in a sub thread to monitor all the files from all the FileMonitor objects
        """
        wm=pyinotify.WatchManager()

        assert(self._killswitch is not None)
        self._handler=ListenEventHandler(self._monitored, self._killswitch.name)
        notifier=pyinotify.Notifier(wm, self._handler)

        dirs:list[str]=[] # all directories containing some monitored file
        for sfile in self._monitored:
            # watch monitored file
            wm.add_watch(sfile.file_path, pyinotify.IN_CLOSE_WRITE) # pyright: ignore
            if _debug:
                syslog.syslog(syslog.LOG_DEBUG, f"FilesMonitor {self} file: {sfile.file_path}")

            # directories need to be monitored in case a file was created after the previous one was deleted (like 'vi' does)
            dir=os.path.dirname(sfile.file_path)
            if dir not in dirs:
                dirs.append(dir)
                if _debug:
                    syslog.syslog(syslog.LOG_DEBUG, f"FilesMonitor {self} DIR {dir}")
                wm.add_watch(dir, pyinotify.IN_CLOSE_WRITE) # pyright: ignore

        # watch killswitch file
        wm.add_watch(self._killswitch.name, pyinotify.IN_CLOSE_WRITE) # pyright: ignore
        if _debug:
            syslog.syslog(syslog.LOG_DEBUG, f"FilesMonitor {self} killswitch {self._killswitch.name}")
            syslog.syslog(syslog.LOG_DEBUG, f"FilesMonitor {self} thread: {threading.current_thread().name}")
        notifier.loop(callback=self._check_must_stop)
        if _debug:
            syslog.syslog(syslog.LOG_DEBUG, f"FilesMonitor {self} loop stopped")

    def register_file(self, sfile:ShadowedFile):
        if self._thread is not None:
            raise Exception(f"FilesMonitor is already running, can't add file '{sfile.file_path}'")
        if _debug:
            syslog.syslog(syslog.LOG_DEBUG, f"FilesMonitor {self} += {sfile.file_path}")
        self._monitored.append(sfile)
        sfile.handle_modified()

class ShadowedResolvFile(ShadowedFile):
    """File monitor for the /etc/resolv.conf file
    Also performs some tricks to handle the NetworkManager actions
    """
    def __init__(self):
        try:
            # try to determine if NetworkManager is present and managing DNS services
            bus=dbus.SystemBus()
            self._nm=bus.get_object("org.freedesktop.NetworkManager", "/org/freedesktop/NetworkManager/DnsManager")
            #self._nm.connect_to_signal("PropertiesChanged", self._nm_prop_changed, dbus_interface="org.freedesktop.DBus.Properties")
            syslog.syslog(syslog.LOG_INFO, "Using DBUS /org/freedesktop/NetworkManager/DnsManager as DNS resolv. source")
        except Exception:
            syslog.syslog(syslog.LOG_INFO, "Using /etc/resolv.conf as DNS resolv. source")
            self._nm=None
        super().__init__("/etc/resolv.conf")

    def handle_modified(self):
        # build the list of actual name servers
        ns_list:list[str]=[]
        if self._nm is None:
            with open("/etc/resolv.conf", "rt") as fd:
                for line in fd.read().splitlines():
                    if line.startswith("nameserver "):
                        try:
                            (_, ns)=line.split()
                            ip=ipaddress.IPv4Address(ns)
                            ns_list.append(str(ip))
                        except Exception:
                            pass # malformed line or IPv6
        else:
            interface=dbus.Interface(self._nm, "org.freedesktop.DBus.Properties")
            conf=interface.Get("org.freedesktop.NetworkManager.DnsManager", "Configuration")
            for item in conf:
                for ns in item["nameservers"]:
                    try:
                        ip=ipaddress.IPv4Address(str(ns))
                        ns_list.append(str(ip))
                    except Exception:
                        pass # malformed line or IPv6

        # write the new name servers
        with open(self.file_shadow, "w") as fd:
            for ns in ns_list:
                fd.write(f"nameserver {ns}\n")

@dataclass
class Features:
    bind_dev:bool=False                     # if True, the whole /dev directory is bound, otherwise, an empty devtmpfs FS is mounted
    mounts:dict[str,dict]|None=None         # dictionary specifying which extra directories have to be mounted, like: 192.168.2.45/32
                                            # {
                                            #    host_path: {
                                            #       "mount-point": mount_path,
                                            #       "read-only": True if read only, False owtherwise>,
                                            #       "monitored": for files only, True if the file will change in the bubble when changed in the host
                                            #                    which means it's possible to use inotify in the bubble (by default, some programs like
                                            #                    VI create another file when writing and the original file remains mounted in the bubble)
                                            # }
    home_dir:str|None=None                  # specify the directory which will be mounted as the home directory of the user in the bubble
    working_dir:str|None=None               # specifies a working directory (defaults to the home directory)
    display_env:dict|None=None              # to pass the XDG_RUNTIME_DIR and/or DISPLAY and/or WAYLAND_DISPLAY are copied to the environment in the bubble
    bind_wayland:bool=False                 # if True, the Wayland socket will be bound to the bubble
    bind_x11:bool=False                     # if True, the X11 socket will be bound to the bubble
    with_host_resolv:bool=False             # if True, /etc/resolv.conf will be mounted in the bubble. If the file changes in the host, the change will
                                            # also be in the bubble. Cf. fairshell-virt-system/manager/vm-manager.py::DNSWatcher
    with_syslog:bool=False                  # if True, the /dev/log file is mapped in the bubble
    users:list[str]|None=None               # list of declared users in the bubble, as GECOS lines
    groups:list[str]|None=None              # list of groups users in the bubble, as /etc/group lines
    capabilities:list[str]|None=None        # list of capabilities to add to the init process running in the bubble
    seccomp_filter_file:str|None=None       # file containing a SECCOMP filter, to be passed AS-IS to bwrap
    with_multimedia:bool=False              # enable multimedia via PipeWire
    extra_env:dict[str,str]|None=None       # some extra environment variables

    with_slirp_tap:bool=False               # True if the slirp4netns tool is started on the host to create a tap device in the bubble and
                                            # enable host port mapping from the host
    slirp_tap_allow_host_access:bool=False  # if True, processes in the bubble can connect to the host's network interface (as 10.0.1.2)
    vde_switch_path:str|None=None           # if not None, attach the bubble to the switch listening @the specified path
    vde_ip_addr:ipaddress.IPv4Interface|None=None # IP address of the bubble if attached to a VDE switch
    bind_medias:bool=False                  # if True, then bind /media/<username>
    with_drm:bool=False                     # allow access to DRM (/dev/dri)
    with_fuse:bool=False                    # allow access to /dev/fuse
    with_pcscd:bool=False                   # allow access to the PCSCD daemon (/run/pcscd/pcscd.comm) for smartcards


    def ensure_consistency(self):
        """Raise an exception if some features are incompatible with others
        """
        if self.bind_dev and not self.with_syslog:
            raise Exception("The 'bind_dev' feature is enabled but not the 'with_syslog' one")

        if self.vde_switch_path and self.vde_ip_addr is None:
            raise Exception("If a VDE path is specified, then a VDE address must also be provided")
        if not self.vde_switch_path and self.vde_ip_addr is not None:
            raise Exception("If a VDE address is specified, then a VDE switch path must also be provided")

        if self.mounts is None:
            self.mounts={}

@dataclass
class DisplayEnvironment:
    runtime_dir:str|None
    wayland_display:str|None
    x11_display:str|None
    x11_socket:str|None
    x11_auth:str|None

def get_display_env() -> DisplayEnvironment:
    rundir=None
    wayland_display=None
    x11_display=None
    x11_socket=None
    x11_xauth=None
    x11_unix_dir="/tmp/.X11-unix"
    if "INVOCATION_ID" in os.environ:
        # we are being run by systemd => get the information from the gnome-shell itself:
        # os.geteiud() -> /run/user/<uid> -> look for the wayland socket -> WAYLAND_DISPLAY
        #                                 -> XDG_RUNTIME_DIR
        #              -> X11 socket in /tmp/.X11-unix/X* -> the process using the socket -> command line arguments for xauth
        euid=os.geteuid()
        rundir=f"/run/user/{euid}"

        # Wayland
        wayland_socket_file=None
        wayland_display=None
        for fname in os.listdir(rundir):
            if fname.startswith("wayland-"):
                path=f"{rundir}/{fname}"
                s=os.stat(path)
                if stat.S_ISSOCK(s.st_mode):
                    if wayland_socket_file is None:
                        wayland_socket_file=path
                        wayland_display=fname
                    else:
                        raise Exception(f"More than one Wayland socket found: '{wayland_socket_file}' and '{path}'")

        # X11
        if os.path.exists(x11_unix_dir) and os.path.isdir(x11_unix_dir):
            path=os.path.join(x11_unix_dir, "X0")
            if os.path.exists(path):
                try:
                    fuser=subprocess.check_output(["fuser", path], stderr=subprocess.DEVNULL).decode()
                    pids=fuser.split()
                    if len(pids)==1:
                        pid=pids[0]
                        p=psutil.Process(int(pid))
                        if p.uids().effective==euid and p.name() in ("gnome-shell", "Xwayland"):
                            # 'gnome-shell' if the Xwayland process has not yet been started, else 'Xwayland'
                            x11_display=":0"
                            x11_socket=path

                            # look for any .mutter-Xwaylandauth.XXX file
                            for fxauthname in os.listdir(rundir):
                                if fxauthname.startswith(".mutter-Xwaylandauth."):
                                    x11_xauth=os.path.join(rundir, fxauthname)
                                    break

                except Exception:
                    # can fail if no process is actually listening to a Unix socket
                    pass

    else:
        # Wayland
        rundir=os.environ.get("XDG_RUNTIME_DIR")
        wayland_display=os.environ.get("WAYLAND_DISPLAY")

        # X11
        x11_display=os.environ.get("DISPLAY")
        x11_xauth=os.environ.get("XAUTHORITY")
        if x11_display and x11_xauth:
            (_, nb)=x11_display.split(":")
            sock=f"{x11_unix_dir}/X{nb}"
            if os.path.exists(sock):
                x11_socket=sock
    if _debug:
        syslog.syslog(syslog.LOG_DEBUG, f"get_display_env({'run by systemd' if 'INVOCATION_ID' in os.environ else 'not run by systemd'}) => {rundir}, {wayland_display}, {x11_display}, {x11_socket}, {x11_xauth}")
    return DisplayEnvironment(runtime_dir=rundir, wayland_display=wayland_display, x11_display=x11_display, x11_socket=x11_socket, x11_auth=x11_xauth)

def _get_pipewire_env() -> tuple[str|None, str|None, str|None]: # XDG_RUNTIME_DIR, pipewire socket, pulse socket
    rundir=None
    if "INVOCATION_ID" in os.environ:
        # we are being run by systemd => get the information from the gnome-shell itself:
        # os.geteiud() -> /run/user/<uid> -> look for the pipewire socket
        euid=os.geteuid()
        rundir=f"/run/user/{euid}"
    else:
        rundir=os.environ.get("XDG_RUNTIME_DIR")

    pw_socket_file=None
    for fname in os.listdir(rundir):
        if fname.startswith("pipewire-") and "manager" not in fname: # we are not a manager, so don't use the -manager socket
            path=f"{rundir}/{fname}"
            s=os.stat(path)
            if stat.S_ISSOCK(s.st_mode):
                if pw_socket_file is None:
                    pw_socket_file=path
                else:
                    raise Exception(f"More than one Pipewire socket found: '{pw_socket_file}' and '{path}'")

    pulse_sock=os.path.join(rundir, "pulse", "native") # pyright: ignore
    pulse_sock=pulse_sock if os.path.exists(pulse_sock) else None

    return (rundir, pw_socket_file, pulse_sock)

def named_netns_create(name:str, init_pid:int) -> str:
    """Create a named network namespace which can then be used by the 'ip netns' command
    NB: using this function needs mount privileges
    """
    mountpoint=f"{system_ns_mountdir}/{name}"
    if os.path.exists(mountpoint):
        return name

    try:
        os.makedirs(system_ns_mountdir, exist_ok=True)
        open(mountpoint, "w") # force the creation of the file
        subprocess.check_call(["mount", "--bind", f"/proc/{init_pid}/ns/net", mountpoint])
        return name
    except Exception as e:
        try:
            os.remove(mountpoint)
        except Exception:
            pass
        raise e

def named_netns_remove(name:str):
    """Does the opposite of named_netns_create()
    NB: using this function needs mount privileges
    """
    mountpoint=f"{system_ns_mountdir}/{name}"
    if os.path.exists(mountpoint):
        try:
            # check if mountpoint is actually mounted
            subprocess.check_call(["findmnt", mountpoint], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) # will raise an exception if not mounted
            subprocess.check_call(["umount", mountpoint], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except subprocess.CalledProcessError:
            pass
        finally:
            try:
                os.remove(mountpoint)
            except Exception:
                pass

class Bubble:
    """Actual implementation of a bubble using namespaces and the BubbleWrap tool,
    and starting the "init" process in the bubble
    """
    def __init__(self, features:Features, run_dir:str|None=None):
        """Create a new bubble.
        The arguments are:
        - @run_dir: the location on the host where the "run dir" for all the processes will be,
          including the socket used by the host to manage the processes in the bubble
        - refer to the Features class for the other arguments
        """
        self._tmpdir=None
        if run_dir is None:
            self._tmpdir=tempfile.TemporaryDirectory()
            self._run_dir=self._tmpdir.name
        elif not os.path.isdir(run_dir):
            raise Exception(f"Path '{run_dir}' does not exist or is not a directory")
        else:
            self._run_dir=run_dir
        self._init_prog=os.path.dirname(os.path.realpath(__file__))+"/init"

        self._overlay_tmpdir=tempfile.TemporaryDirectory()

        self._bubble_pid:int|None=None
        self._files_monitor:FilesMonitor=FilesMonitor()
        self._wl_proxy_pid:int|None=None

        self._state=BubbleState.INITIALIZED
        self._started_ts:datetime.datetime|None=None

        self._popen_slirp=None
        self._slirp4netns_sock=None

        self._mapped_ports={} # key=ID returned by slirp4netns, value=[host port, spawned port, proto]

        features.ensure_consistency()
        self._features=features

    def __del__(self):
        try:
            if _debug and self._bubble_pid is not None:
                syslog.syslog(syslog.LOG_DEBUG, f"Bubble with PID {self._bubble_pid} is being destroyed")
            if self._tmpdir is not None:
                self._tmpdir.cleanup()
                self._tmpdir=None
            if self._overlay_tmpdir is not None:
                self._overlay_tmpdir.cleanup()
                self._overlay_tmpdir=None
            self.destroy()
        except Exception:
            pass

    @property
    def state(self) -> BubbleState:
        return self.get_state()

    def get_state(self) -> BubbleState:
        """Get the state of the bubble"""
        if self._state==BubbleState.RUNNING:
            if not os.path.exists(f"{self._run_dir}/bubble.sock"):
                if self._bubble_pid is not None:
                    os.kill(self._bubble_pid, signal.SIGKILL)
                    os.waitpid(self._bubble_pid, 0)
                    self._bubble_pid=None
                self._state=BubbleState.TERMINATED
            elif self._bubble_pid is not None:
                p=psutil.Process(self._bubble_pid)
                if not p.is_running():
                    os.waitpid(self._bubble_pid, 0)
                    self._bubble_pid=None
                    self._state=BubbleState.TERMINATED
            #syslog.syslog(syslog.LOG_ERR, f"Bubble with PID {self._bubble_pid} state: {self._state}")
        return self._state

    @property
    def init_prog(self) -> str:
        return self._init_prog

    @init_prog.setter
    def init_prog(self, path:str):
        if os.path.isabs(path):
            self._init_prog=path
        else:
            self._init_prog=os.path.realpath(os.path.dirname(os.path.realpath(__file__))+"/"+path)

    @property
    def running_duration(self) -> int|None:
        """Get the running duration in seconds, or None if bubble is not running
        """
        if self._state==BubbleState.RUNNING:
            now=datetime.datetime.now(datetime.timezone.utc)
            return (now-self._started_ts).seconds # pyright: ignore
        return None

    @property
    def run_dir(self) -> str:
        return self._run_dir

    @property
    def init_pid(self) -> int|None:
        """Get the PID of the spawned init process (in the 'host' pid namespace, otherwise it is always 1)
        Returns None if the bubble is not started or if the init process is not yet started
        """
        if self._bubble_pid is None:
            return None
        p=psutil.Process(self._bubble_pid)
        children=p.children()
        if len(children)==0:
            return None
        if len(children)!=1:
            raise Exception(f"WTF: bwrap seems to have more than one child: {children}")
        return children[0].pid

    @property
    def mnt_namespace(self) -> str:
        """Get the mount namespace of the bubble"""
        if self._state!=BubbleState.RUNNING:
            raise Exception(f"Bubble is {self._state.value}")
        if self.init_pid is None:
            raise Exception("Bubble's init process is not running")
        return os.readlink(f"/proc/{self.init_pid}/ns/mnt")

    @property
    def net_namespace(self) -> str:
        """Get the net namespace of the bubble"""
        if self._state!=BubbleState.RUNNING:
            raise Exception(f"Bubble is {self._state.value}")
        if self.init_pid is None:
            raise Exception("Bubble's init process is not running")
        return os.readlink(f"/proc/{self.init_pid}/ns/net")

    @property
    def net_namespace_raw(self) -> str:
        """Get the string representing the net namespace of the bubble
        Taken from readlink /proc/XXX/ns/net: "net:[4026531840]" --> "4026531840"
        """
        if self._state!=BubbleState.RUNNING:
            raise Exception(f"Bubble is {self._state.value}")
        return self.net_namespace[5:-1]

    def _dir_args(self, bound_dirs:list[str]) -> list[str]:
        """Set up directories overlays and return all the arguments for bwrap"""
        if self._overlay_tmpdir is None:
            raise Exception("CODEBUG: _dir_args() called after destroy()")

        @dataclass
        class MountPoint:
            host_path:str|None
            mount_path:str
            readonly:bool=True
            monitored:bool=False

            def __str__(self) -> str:
                return f"{self.mount_path} <== {self.host_path} ({'RO' if self.readonly else 'RW'}{', MOINT' if self.monitored else ''})"

            def __post_init__(self):
                if not self.mount_path:
                    raise Exception("CODEBUG: empty mount path")
                forced_dir=self.mount_path[-1]=="/"
                self.mount_path=os.path.realpath(self.mount_path)
                if forced_dir:
                    self.mount_path+="/"
                if self.host_path is not None and self.host_path[0]=="-": # hack for now
                    self.host_path=None
                if self.host_path is not None:
                    self.host_path=os.path.realpath(self.host_path)
                    if os.path.isdir(self.host_path):
                        self.mount_path=self.mount_path+"/"
                        self.host_path=self.host_path+"/"

            @classmethod
            def from_data(cls, host_path:str, info:dict)->MountPoint:
                if not isinstance(info, dict):
                    raise Exception(f"Invalid mountpoint info {info}")
                mp=info.get("mount-point")
                ro=info.get("read-only", True)
                monit=info.get("monitored", False)
                if (not isinstance(mp, str) or mp=="") or \
                    not isinstance(ro, bool) or \
                    monit is not None and not isinstance(monit, bool):
                    raise Exception(f"Invalid mountpoint info {info}: expected a dict")
                return cls(host_path, mp, ro, False if monit is None else monit)

            def prefix_path(self, prefix:str) -> str:
                return os.path.join(prefix, self.mount_path[1:])

        @dataclass
        class MountPointGroup:
            """Group mount points beneath a common top directory"""
            items: list[MountPoint]
            mount_path:str

            def __str__(self) -> str:
                return f"{self.mount_path} <== [{', '.join([str(mpoint) for mpoint in self.items])}]"

            def __post_init__(self):
                if len(self.items)==0:
                    raise Exception("CODEBUG: MountPointGroup has no MountPoint")

            def add(self, mpoint:MountPoint):
                if self.mount_path.startswith(mpoint.mount_path):
                    self.mount_path=mpoint.mount_path
                    self.items=[mpoint]+self.items
                else:
                    self.items.append(mpoint)

            def get_args(self, run_dir:str, tmp_dir:str) -> list[str]:
                if len(self.items)==0:
                    raise Exception("CODEBUG: MountPointGroup has no MountPoint")

                args:list[str]=[]
                if len(self.items)==1:
                    mpoint=self.items[0]
                    if mpoint.host_path is None:
                        dir=mpoint.prefix_path(run_dir)
                        if _debug:
                            syslog.syslog(syslog.LOG_DEBUG, f"MAKEDIR {dir}")
                        os.makedirs(dir)
                        if mpoint.readonly:
                            args+=["--ro-bind", dir, mpoint.mount_path]
                        else:
                            args+=["--bind", dir, mpoint.mount_path]
                    elif mpoint.readonly:
                        args+=["--ro-bind", mpoint.host_path, mpoint.mount_path]
                    elif os.access(mpoint.host_path, os.W_OK):
                        # directly bind file if we have write permission
                        args+=["--bind", mpoint.host_path, mpoint.mount_path]
                    elif os.path.isdir(mpoint.host_path):
                        # add an overlay to allow write to directory
                        ovl_dir=mpoint.prefix_path(run_dir)
                        if _debug:
                            syslog.syslog(syslog.LOG_DEBUG, f"MAKEDIR 2 {ovl_dir}")
                        os.makedirs(ovl_dir)
                        args+=[
                            "--overlay-src", mpoint.host_path,
                            "--overlay-src", ovl_dir,
                            "--tmp-overlay", mpoint.mount_path
                        ]
                    else:
                        raise Exception(f"Can't allow RW acces to file '{mpoint.host_path}' which is read-only (use a directory instead)")
                else:
                    # use the 1st mpoint as the base of the overlay
                    f_mpoint=self.items[0]
                    if f_mpoint.host_path is not None:
                        args+=["--overlay-src", f_mpoint.host_path]

                    ref_dir=f_mpoint.prefix_path(run_dir) # writable layer
                    if _debug:
                        syslog.syslog(syslog.LOG_DEBUG, f"MAKEDIR REF_DIR {ref_dir}")
                    os.makedirs(ref_dir)
                    args+=["--overlay-src", ref_dir]

                    # copy the contents of all the other mount points (using bind mount would be better but would require root privs.)
                    for mpoint in self.items[1:]:
                        if not mpoint.mount_path.startswith(self.mount_path):
                            raise Exception(f"CODEBUG: mount point {mpoint.mount_path} not a sub dir. of group's {self.mount_path}")
                        if mpoint.host_path is not None:
                            delta_path=mpoint.mount_path[len(self.mount_path):]
                            if delta_path[0]=="/":
                                delta_path=delta_path[1:]
                            dest_path=os.path.join(ref_dir, delta_path)
                            os.makedirs(os.path.dirname(dest_path), exist_ok=True)
                            if os.path.isdir(mpoint.host_path):
                                if os.path.exists(dest_path):
                                    if _debug:
                                        syslog.syslog(syslog.LOG_DEBUG, f"COPY recurs. contents of {mpoint.host_path} to {dest_path}")
                                    for fname in os.listdir(mpoint.host_path):
                                        fpath=os.path.join(mpoint.host_path, fname)
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
                                        syslog.syslog(syslog.LOG_DEBUG, f"COPY tree {mpoint.host_path} to {dest_path}")
                                    shutil.copytree(mpoint.host_path, dest_path)
                            elif os.path.exists(mpoint.host_path):
                                if _debug:
                                    syslog.syslog(syslog.LOG_DEBUG, f"COPY file {mpoint.host_path} to {dest_path}")
                                shutil.copy2(mpoint.host_path, dest_path, follow_symlinks=False)
                            else:
                                syslog.syslog(syslog.LOG_ERR, f"Can't copy mount point '{mpoint.host_path}' to '{dest_path}': does not exist")

                    if f_mpoint.readonly:
                        rw_dir=f_mpoint.prefix_path(tmp_dir)+"_._rw" # working layer
                        if _debug:
                            syslog.syslog(syslog.LOG_DEBUG, f"MAKEDIR RW_DIR {rw_dir}")
                        os.makedirs(rw_dir)
                        os.chmod(rw_dir, 0o555)
                        work_dir=f_mpoint.prefix_path(tmp_dir)+"_._wo" # working layer
                        if _debug:
                            syslog.syslog(syslog.LOG_DEBUG, f"MAKEDIR WORK_DIR {work_dir}")
                        os.makedirs(work_dir)
                        args+=["--overlay", rw_dir, work_dir, self.mount_path]
                    else:
                        args+=["--tmp-overlay", self.mount_path]
                return args


        # prepare list of directories which will either be RO-mounted AS-IS, or will be the base of an
        # overlay if we have mount points beneath them
        groups:dict[str,MountPointGroup]={} # key=mount point
        for item in bound_dirs:
            mpoint=MountPoint(item, item)
            grp=MountPointGroup([mpoint], mpoint.mount_path)
            groups[grp.mount_path]=grp

        if self._features.mounts is not None:
            # compute MountPoint objects, to be removed when improved API
            mpoints:list[MountPoint]=[]
            for hpath, info in self._features.mounts.items():
                mpoints.append(MountPoint.from_data(hpath, info))

            # group mount points in overlays
            for mpoint in mpoints:
                if _debug:
                    syslog.syslog(syslog.LOG_DEBUG, f"HANDLE mpoint={str(mpoint)}" )
                found=False
                for path in list(groups.keys()).copy():
                    grp=groups[path]
                    if mpoint.mount_path==grp.mount_path:
                        pass
                    elif mpoint.mount_path.startswith(grp.mount_path): # mpoint is a sub directory or grp
                        grp.add(mpoint)
                        found=True
                        break
                    elif grp.mount_path.startswith(mpoint.mount_path): # mpoint is a parent directory of grp
                        ngrp=MountPointGroup([mpoint]+grp.items, mpoint.mount_path)
                        groups[ngrp.mount_path]=ngrp
                        del groups[grp.mount_path]
                        found=True
                        break
                if not found:
                    grp=MountPointGroup([mpoint], mpoint.mount_path)
                    groups[grp.mount_path]=grp

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

            for (path, grp) in groups.items():
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
                    egrp.items+=grp.items
            groups=ngroups

            if _debug:
                for grp in groups.values():
                    syslog.syslog(syslog.LOG_DEBUG, f"group={str(grp)}")

        # compute args from overlays
        args:list[str]=[]
        for grp in groups.values():
            args+=grp.get_args(self._run_dir, self._overlay_tmpdir.name)
        return args

    def _start_bubble(self):
        """Actually run the bwrap program to start the bubble
        @run_dir is the directory where the fake passwd and group files are creates and which is mounted
        as /bubble/run
        """
        if self._bubble_pid is not None or self._wl_proxy_pid is not None:
            raise Exception("Code bug: bubble PID or Wayland proxy is already set up")

        # set up fake passwd and group files
        misc_dir=f"{self._run_dir}/misc"
        os.makedirs(misc_dir, exist_ok=True)
        passwd_file=f"{misc_dir}/passwd"
        group_file=f"{misc_dir}/group"
        with open(passwd_file, "w") as fd:
            user_def=pwd.getpwuid(os.geteuid())
            fd.write(f"{user_def.pw_name}:x:{user_def.pw_uid}:{user_def.pw_gid}:{user_def.pw_gecos}:/home/{user_def.pw_name}:{user_def.pw_shell}\n")
            if self._features.users is not None:
                for line in self._features.users:
                    fd.write(line+"\n")
            bhome_dir=f"/home/{user_def.pw_name}"

        with open(group_file, "w") as fd:
            group=grp.getgrgid(os.getegid())
            fd.write(f"{group.gr_name}:x:{group.gr_gid}:{','.join(group.gr_mem)}\n")
            if self._features.users is not None:
                for line in self._features.users:
                    fd.write(line+"\n")

        # start the sandbox
        # NB: the --dev bwrap option creates an intermediary namespace and the final namespace
        args=[
            "/usr/bin/bwrap",
            "--new-session", # cf. CVE-2017-5226 and https://github.com/flatpak/flatpak/commit/902fb713990a8f968ea4350c7c2a27ff46f1a6c4
            "--as-pid-1",
            "--unshare-pid",
            "--unshare-net",
            "--unshare-ipc",
            "--dir", "/tmp",
            "--dir", "/var",
            "--symlink", "../tmp", "var/tmp",
            "--proc", "/proc",
            "--ro-bind", self._init_prog, "/bubble/init",
            "--bind", self._run_dir, "/bubble/run",
            "--tmpfs", "/tmp"
        ]

        # home directory bind?
        if self._features.home_dir is not None:
            args+=["--bind", self._features.home_dir, bhome_dir]
        else:
            args+=["--tmpfs",  bhome_dir]

        # mount directories
        # the bound_dirs var. is the list dirs which are RO bound from the host (excluding /dev, /run and /sys) for
        # which there might be conflicts with features's mount points if directly --ro-bind
        bound_dirs=["/etc/fonts", "/etc/xdg", "/etc/alternatives", "/etc/ssl", "/usr"]
        args+=self._dir_args(bound_dirs)
        args+=[
            "--symlink", "usr/lib", "/lib",
            "--symlink", "usr/lib64", "/lib64",
            "--symlink", "usr/bin", "/bin",
            "--symlink", "usr/sbin", "/sbin",
            "--symlink", "/run", "/var/run",
            "--ro-bind", "/var/lib/aspell", "/var/lib/aspell",
            "--die-with-parent",
            "--clearenv",
            "--chdir", self._features.working_dir if self._features.working_dir else bhome_dir,
            "--ro-bind", passwd_file, "/etc/passwd",
            "--ro-bind", group_file, "/etc/group"
        ]

        if self._features.bind_medias:
            medias_dir=os.path.join("/media", user_def.pw_name)
            if os.path.exists(medias_dir):
                args+=["--bind", medias_dir, medias_dir]

        if self._features.bind_dev:
            args+=["--dev-bind", "/dev", "/dev"]
        else:
            args+=[
                "--dev", "/dev",
                "--dev-bind", "/dev/net/tun", "/dev/net/tun",
                "--dev-bind", "/dev/bus/usb", "/dev/bus/usb"
            ]
            if self._features.with_drm:
                args+=[
                    "--dev-bind", "/dev/dri", "/dev/dri",
                    "--ro-bind", "/sys/dev/char", "/sys/dev/char",
                    "--ro-bind", "/sys/devices/pci0000:00", "/sys/devices/pci0000:00"
                ]
            if self._features.with_fuse:
                args+=["--dev-bind", "/dev/fuse", "/dev/fuse"]

        if self._features.with_pcscd:
            if os.path.exists("/run/pcscd"):
                # we need to mount the whole directory because the PCSCD daemon will stop and when it re-starts, it
                # creates a new socket and there is not now any way to do this
                args+=["--bind", "/run/pcscd", "/run/pcscd"]

                # may be needed in some cases?
                #if not self._features.bind_dev:
                #    args+=["--dev-bind", "/dev/bus", "/dev/bus"]
            else:
                syslog.syslog(syslog.LOG_WARNING, "Can't enable PCSCD as daemon is not installed or running, skipping")

        # /dev/log is often a symlink to /run/systemd/journal/dev-log
        if self._features.with_syslog:
            logfile=os.path.realpath("/dev/log")
            if logfile=="/dev/log":
                if not self._features.bind_dev:
                    args+=["--bind", logfile, logfile]
            else:
                args+=["--bind", logfile, logfile]
                if not self._features.bind_dev:
                    args+=["--symlink", logfile, "/dev/log"]

        # environment variables, which cannot override the ones we hard code later
        if self._features.extra_env is not None:
            for k,v in self._features.extra_env.items():
                if not isinstance(k, str) or not isinstance(v, str):
                    raise Exception("Extra environment variables names and values must be strings")
                args+=["--setenv", k, v]

        args+=[
            "--setenv", "HOME", bhome_dir,
            "--setenv", "XDG_RUNTIME_DIR", "/bubble/run",
        ]

        cap_net_admin=False
        if self._features.vde_switch_path:
            cap_net_admin=True
            args+=["--bind", self._features.vde_switch_path, VDESwitch.bubble_switch_path]

        # capabilities
        capabilities=set([capname.lower() for capname in (self._features.capabilities if self._features.capabilities is not None else [])])
        if cap_net_admin and "net_admin" not in capabilities:
            capabilities.add("net_admin")

        for capname in capabilities:
            if capname not in ("net_raw", "net_admin", "net_bind_service", "sys_admin", "sys_chroot", "sys_rawio"):
                raise Exception(f"CODEBUG: hnhandled capability '{capname}'")
            args+=["--cap-add", f"CAP_{capname.upper()}"]

        # map /etc/resolv.conf file from the host?
        if self._features.with_host_resolv:
            sfile=ShadowedResolvFile()
            self._files_monitor.register_file(sfile)
            args+=["--ro-bind", sfile.file_shadow, "/etc/resolv.conf"]

        # LANG env. variable
        lang=os.environ.get("LANG")
        if lang is not None:
            args+=["--setenv", "LANG", lang]

        # access to display
        if self._features.display_env:
            args+=["--setenv", "XDG_SESSION_TYPE", "wayland"]
            for key,value in self._features.display_env.items():
                args+=[
                    "--setenv", key, value
                ]

        denv:DisplayEnvironment|None=None
        if self._features.bind_wayland:
            denv=get_display_env()
            if denv.runtime_dir is not None and denv.wayland_display is not None:
                args+=[
                    "--bind", f"{denv.runtime_dir}/{denv.wayland_display}", f"/bubble/run/{denv.wayland_display}",
                    "--setenv", "WAYLAND_DISPLAY", denv.wayland_display
                ]

        if self._features.bind_x11:
            if denv is None:
                denv=get_display_env()
            if denv.x11_display is not None and denv.x11_auth is not None and denv.x11_socket is not None:
                args+=[
                    "--bind", denv.x11_socket, denv.x11_socket,
                    "--setenv", "DISPLAY", denv.x11_display,
                    "--bind", denv.x11_auth, "/bubble/run/.Xauth",
                    "--setenv", "XAUTHORITY", "/bubble/run/.Xauth"
                ]

        # access to multimedia
        if self._features.with_multimedia:
            (_, pw_socket, pulse_sock)=_get_pipewire_env()
            if pw_socket is None:
                syslog.syslog(syslog.LOG_ERR, "Could not identify Pipewire socket, apps in bubble won't have access to Pipewire server")
            else:
                sockname=os.path.basename(pw_socket)
                args+=[
                    "--bind", pw_socket, os.path.join("/bubble/run", sockname),
                    "--bind", f"{pw_socket}.lock", os.path.join("/bubble/run", f"{sockname}.lock")
                ]
            if pulse_sock is not None:
                args+=[
                    "--bind", pulse_sock, os.path.join("/bubble/run", "pulse", os.path.basename(pulse_sock)),
                    "--ro-bind", "/etc/pulse", "/etc/pulse"
                ]

        # SECCOMP
        fdr=None
        fdw=None
        if self._features.seccomp_filter_file is not None:
            (fdr, fdw)=os.pipe()
            args+=["--seccomp", str(fdr)]

        # specify the bubble's init program and its arguments
        args+=["/bubble/init", "/bubble/run"]
        if self._features.vde_ip_addr is not None:
            args+=["--vde-interface", "vde0", "--vde-address", str(self._features.vde_ip_addr)]
        if len(capabilities)>0:
            args+=["--cap-add", ",".join(capabilities)]

        # start the bubblewrap process. We don't use the subprocess module here because
        # bwrap does not like the idea of being started with an effective UID!=0 and a real UID=0: it fails
        # with the "Unexpected setuid user XXX, should be 0" error
        #syslog.syslog(syslog.LOG_DEBUG, f"BUBBLE args: {json.dumps(args, indent=4)}")
        argsstr=" ".join(args)
        if _debug:
            syslog.syslog(syslog.LOG_DEBUG, f"Running bwrap: {argsstr}")
        (pipe_read, pipe_write)=os.pipe()
        pid=os.fork()
        if pid==0:
            # in the forked child
            try:
                # UID changes
                euid=os.geteuid()
                if euid!=os.getuid() and os.getuid()==0:
                    os.seteuid(0)
                    os.setgid(os.getegid())
                    os.setuid(euid)

                # actual running
                if fdw is not None:
                    os.close(fdw)
                    fdw=None
                if fdr is not None:
                    os.set_inheritable(fdr, True)
                os.close(pipe_read)
                os.dup2(pipe_write, sys.stderr.fileno())
                os.setpgid(0, 0) # to avoid sharing the same process group as the calling process (so that children don't get the CTRL-C for example)
                os.execv(args[0], args)
            except FileNotFoundError:
                raise Exception(f"File '{args[0]}' is not present")
        elif pid==-1:
            raise Exception("Could not fork() to start bubblewrap")
        else:
            # in the original process
            if fdr is not None:
                os.close(fdr)
            if self._features.seccomp_filter_file is not None and fdw is not None:
                nb=os.write(fdw, open(self._features.seccomp_filter_file, "rb").read())
                syslog.syslog(syslog.LOG_INFO, f"Written seccomp filter to fd ({nb} bytes)")

            # wait for bubblewrap to have spawned its child or died prematurely
            p=psutil.Process(pid)
            while True:
                try:
                    p.wait(0.1)
                    os.close(pipe_write)
                    err=os.read(pipe_read, 1000)
                    raise Exception(f"Failed to start 'bwrap': {err}")
                except psutil.TimeoutExpired:
                    pass
                if len(p.children())>0:
                    if _debug:
                        syslog.syslog(syslog.LOG_DEBUG, f"bwrap (pid {pid}) and enclosed init programs started")
                    self._files_monitor.start_monitoring()
                    self._bubble_pid=pid
                    return

    def setup(self):
        """Actually set up the bubble"""
        if self._state!=BubbleState.INITIALIZED:
            raise Exception("Bubble state does not allow to perform another setup")

        try:
            self._start_bubble()
            if self._features.with_slirp_tap or self._features.vde_switch_path:
                self.wait_for_init_started()
                self.activate_tap_network()
            self._state=BubbleState.RUNNING
            self._started_ts=datetime.datetime.now(datetime.timezone.utc)
        except Exception as e:
            self.destroy()
            raise e

    def wait_for_init_started(self, max_delay=None):
        """Wait for the bubble's init process to be started
        If @max_delay is None, wait forever, otherwise after @may_delay (in miliseconds), an
        exception is raised
        """
        delay=200
        total=0
        pid=self.init_pid
        if pid is not None:
            return

        while True:
            time.sleep(delay/2000)
            total+=delay
            if max_delay is not None and delay>max_delay:
                raise Exception("Bubble failed to be set up or could not connect to bubble")
            pid=self.init_pid
            if pid is not None:
                return

    def destroy(self):
        """Destroy the bubble"""
        self._files_monitor.stop_monitoring()

        if self._popen_slirp is not None:
            self._popen_slirp.kill()
            self._popen_slirp.wait()
            self._popen_slirp=None
        if self._bubble_pid is not None:
            # avoid zombies
            try:
                os.kill(self._bubble_pid, signal.SIGKILL)
            except Exception:
                pass
            os.waitpid(self._bubble_pid, 0)
            self._bubble_pid=None
        if self._wl_proxy_pid is not None:
            # avoid zombies
            try:
                os.kill(self._wl_proxy_pid, signal.SIGKILL)
            except Exception:
                pass
            os.waitpid(self._wl_proxy_pid, 0)
            self._wl_proxy_pid=None
        self._slirp4netns_sock=None
        self._mapped_ports={}
        self._state=BubbleState.TERMINATED

    def deactivate_tap_network(self):
        """If the TAP feature was specified, then stops networking"""
        if not self._features.with_slirp_tap and not self._features.vde_switch_path:
            raise Exception("Networking in the bubble has not been activated")

        # slirp TAP
        if self._features.with_slirp_tap and self._slirp4netns_sock is not None:
            if self._popen_slirp is not None:
                self._popen_slirp.kill()
                self._popen_slirp.wait()
                self._popen_slirp=None
            self._slirp4netns_sock=None

        # VDE network interface
        if self._features.vde_switch_path:
            api=BubbleAPI(self._run_dir)
            api.wait_for_bubble_ready()
            api.deactivate_vde_networking()

    def activate_tap_network(self):
        """If the TAP feature was specified, then starts networking"""
        if not self._features.with_slirp_tap and not self._features.vde_switch_path:
            raise Exception("Networking in the bubble has not been activated")

        # slirp TAP
        if self._features.with_slirp_tap and self._slirp4netns_sock is None:
            try:
                self._slirp4netns_sock=f"{self._run_dir}/slirp4netns.socket"
                args=["slirp4netns", "--api-socket", self._slirp4netns_sock, "--configure",
                    "--mtu=65520", "--disable-dns"]
                if not self._features.slirp_tap_allow_host_access:
                    args.append("--disable-host-loopback")
                args+=[f"{self.init_pid}", "--cidr", "10.0.1.0/24", "tap0"]
                self._popen_slirp=subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

                # wait for the socket to be present
                self._send_command_to_slirp4netns_socket({
                    "execute": "list_hostfwd"
                })
            except Exception as e:
                if self._popen_slirp is not None:
                    self._popen_slirp.kill()
                    self._popen_slirp.wait()
                    self._popen_slirp=None
                self._slirp4netns_sock=None
                raise e

        # VDE network interface
        if self._features.vde_switch_path:
            api=BubbleAPI(self._run_dir)
            api.wait_for_bubble_ready()
            api.activate_vde_networking()

    def port_map(self, host_port:int, bubble_port:int, proto:str="tcp"):
        """Map a port of the spawned process to the host's loopback interface"""
        if not self._features.with_slirp_tap:
            raise Exception("Networking in the bubble with a slirp tap has not been activated")

        if self._slirp4netns_sock is None:
            raise Exception("Slirp4netns process has not been spawned")

        command={
            "execute": "add_hostfwd",
            "arguments": {
                "proto": proto,
                "host_addr": "0.0.0.0", # all interfaces so it can be reached by other processes spawned in the same way
                "host_port": host_port,
                "guest_addr": "10.0.1.100",
                "guest_port": bubble_port
            }
        }
        response=self._send_command_to_slirp4netns_socket(command)
        retdata=response.get("return")
        if retdata is None:
            self._raise_exception_from_slirp4netns_socket_command_error(response, f"Could not map host port {host_port} to spawned process's port {bubble_port}")
        id=retdata.get("id")
        if id is None:
            raise Exception(f"Code bug: could not ID of mapping from host port {host_port} to spawned process's port {bubble_port} (returned {retdata})")
        self._mapped_ports[id]=[host_port, bubble_port, proto]

    def _send_command_to_slirp4netns_socket(self, command:dict):
        if self._slirp4netns_sock is None:
            raise Exception("Slirp4netns process has not been spawned")
        client=socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)

        # we might have to wait a bit for the slirp4netns program to be operational
        counter=0
        while True:
            try:
                client.connect(self._slirp4netns_sock)
                break
            except (ConnectionRefusedError, FileNotFoundError) as e:
                counter+=1
                if counter>10:
                    raise e
                time.sleep(0.1)

        msg=json.dumps(command)
        client.sendall(msg.encode())
        response=client.recv(1024)
        data=json.loads(response.decode())
        client.close()
        return data

    def _raise_exception_from_slirp4netns_socket_command_error(self, response, context:str):
        erdata=response.get("error")
        if erdata is not None:
            descr=erdata.get("desc")
            if descr is not None:
                raise Exception(f"{context}: {descr}")
        raise Exception("CODEBUG: unhandled case in _raise_exception_from_slirp4netns_socket_command_error")

    def port_unmap(self, host_port:int|None=None, bubble_port:int|None=None, proto:str="tcp"):
        """Unmap a mapped port.
        At least @host_port or @bubble_port have to be specified.
        NB: If multiple host ports are mapped to the same @bubble_port, and the host port is not specified, then only the 1st one will be unmapped
        """
        if not self._features.with_slirp_tap:
            raise Exception("Networking in the bubble with a slirp tap has not been activated")
        slirpid=None
        if host_port is None:
            if bubble_port is None:
                raise Exception("host port or spawned port not specified")
            for id, value in self._mapped_ports.items():
                if value[1]==bubble_port and value[2]==proto:
                    slirpid=id
                    break
        else:
            if bubble_port is None:
                for id, value in self._mapped_ports.items():
                    if value[0]==host_port and value[2]==proto:
                        slirpid=id
                        break
            else:
                for id, value in self._mapped_ports.items():
                    if value[0]==host_port and value[1]==bubble_port and value[2]==proto:
                        slirpid=id
                        break
        if slirpid is None:
            raise Exception("No port mapping found")
        command={
            "execute": "remove_hostfwd",
            "arguments": {"id": slirpid}
        }
        response=self._send_command_to_slirp4netns_socket(command)
        retdata=response.get("return")
        if retdata is None:
            self._raise_exception_from_slirp4netns_socket_command_error(response, f"Could not map host port {host_port} to spawned process's port {bubble_port}")
        if retdata=={}:
            del self._mapped_ports[slirpid]
        else:
            raise Exception(f"Code bug: could not ID of mapping from host port {host_port} to spawned process's port {bubble_port} (returned {retdata})")

    def get_mapped_ports(self) -> dict[int, int]:
        """Get the mapped ports as a dictionary indexed by the host port, and with values being the spawned process's mapped port"""
        if not self._features.with_slirp_tap:
            raise Exception("Networking in the bubble has not been activated")
        res={}
        for id, (host_port, bubble_port, proto) in self._mapped_ports.items():
            res[host_port]=bubble_port
        return res

    def named_netns_create(self) -> str:
        """Create a named network namespace which can then be used by the 'ip netns' command
        NB: using this function needs mount privileges
        """
        pid=self.init_pid
        if pid is None:
            raise Exception("Bubble is not yet started (no init process)")
        return named_netns_create(self.net_namespace_raw, pid)

    def named_netns_remove(self):
        """Does the opposite of named_netns_create()
        NB: using this function needs mount privileges
        """
        named_netns_remove(self.net_namespace_raw)

    def map_host_pid_to_bubble(self, pid:int) -> int:
        """Get the PID in the bubble of the process specified by its PID in the "host" namespace
        (the namespace in which the bubble has been created)
        """
        if self._state!=BubbleState.RUNNING:
            raise Exception(f"Bubble is {self._state.value}")
        bubblens=os.readlink(f"/proc/{self.init_pid}/ns/pid")
        try:
            proc_pid_ns=os.readlink(f"/proc/{pid}/ns/pid")
        except FileNotFoundError:
            raise Exception(f"Process with host {pid} does not exist")
        if proc_pid_ns!=bubblens:
            raise Exception(f"Process with host {pid} is not running in the bubble")

        with open(f"/proc/{pid}/status", "r") as fd:
            for line in fd.readlines():
                if line.startswith("NSpid:"):
                    data=line[6:].strip()
                    try:
                        (hpid, bpid, *_)=data.split()
                        return int(bpid)
                    except ValueError:
                        raise Exception(f"NSpid line '{line}' does not list all the PIDs in all the namespaces")
        raise Exception(f"No NSpid line in /proc/{pid}/status???")

    def map_bubble_pid_to_host(self, pid:int) -> int|None:
        """Get the PID in the "host" namespace (the namespace in which the bubble has been created) of the
        process specified by its PID in the bubble
        """
        spid=str(pid)
        bubblens=os.readlink(f"/proc/{self.init_pid}/ns/pid")
        for fname in os.listdir("/proc"):
            try:
                hpid=int(fname)
            except Exception:
                # we don't care about non PID directories in /proc
                continue
            try:
                if os.readlink(f"/proc/{hpid}/ns/pid")==bubblens:
                    with open(f"/proc/{hpid}/status", "r") as fd:
                        for line in fd.readlines():
                            if line.startswith("NSpid:"):
                                data=line[6:].strip()
                                try:
                                    (hpid, bpid, *_)=data.split()
                                    if bpid==spid:
                                        return int(hpid)
                                except ValueError:
                                    raise Exception(f"NSpid line '{line}' does not list all the PIDs in all the namespaces")
            except PermissionError:
                pass
        return None

    def is_host_pid_beneath_bubble(self, pid:int) -> bool:
        """Tell if a process specified by its PID in the "host" namespace is in the bubble
        or any bubble itself in it
        """
        if self._state!=BubbleState.RUNNING:
            raise Exception(f"Bubble is {self._state.value}")
        init_pid=self.init_pid
        proc=psutil.Process(pid)
        while proc is not None and proc.pid!=1:
            if proc.pid==init_pid:
                return True
            proc=proc.parent()
        return False

class VDESwitch(Bubble):
    """Bubble which implements a VDE switch in a specific directory"""
    bubble_switch_path="/bubble/run/switch"

    def __init__(self, switch_run_dir:str|None=None):
        self._swtmpdir=None
        if switch_run_dir is None:
            self._swtmpdir=tempfile.TemporaryDirectory()
            switch_run_dir=self._swtmpdir.name

        mounts={
            switch_run_dir: {
                "mount-point": switch_run_dir,
                "read-only": False,
                "monitored": False
            }
        }
        features=Features(mounts=mounts)
        super().__init__(features=features, run_dir=switch_run_dir)

    def __del__(self):
        self._swtmpdir=None

    @property
    def host_switch_path(self):
        """Path of the switch (in the host)"""
        return f"{self.run_dir}/switch"

    def setup(self):
        super().setup()
        api=BubbleAPI(self._run_dir)
        api.wait_for_bubble_ready()
        try:
            shutil.rmtree(self.host_switch_path)
        except FileNotFoundError:
            pass
        api.start_process(["vde_plug", f"switch://{VDESwitch.bubble_switch_path}", "null://"])

class ProcessNotYetTerminatedException(Exception):
    pass

class BubbleAPI:
    """Class to manage a bubble represented by a Bubble object from the host using the unix socket
    set up by a Bubble object
    """
    timeout=1

    def __init__(self, run_dir:str):
        """Refer to the Bubble object's documentation for the meaning of the run_dir argument"""
        self._run_dir=run_dir
        self._socket=f"{run_dir}/bubble.sock"
        self._q_socket=parse.quote_plus(self._socket)
        self._session=requests_unixsocket.Session()

    def _common_check(self, path):
        if path[0]!="/":
            raise Exception(f"Invalid path '{path}'")
        if self.state!=BubbleState.RUNNING:
            raise Exception(f"Bubble is {self.state.value}")

    def _get(self, path:str, params:dict|None=None):
        self._common_check(path)
        resp=self._session.get(f"http+unix://{self._q_socket}{path}",
            params=params, timeout=BubbleAPI.timeout)
        if resp.ok:
            return self._handle_response_generated_exception(resp.json())
        raise Exception(resp.text)

    def _post(self, path:str, data):
        self._common_check(path)
        resp=self._session.post(f"http+unix://{self._q_socket}{path}",
            data=json.dumps(data), headers={"Content-Type": "application/json"},
            timeout=BubbleAPI.timeout)
        if resp.ok:
            return self._handle_response_generated_exception(resp.json())
        raise Exception(resp.text)

    def _put(self, path:str, data):
        self._common_check(path)
        resp=self._session.put(f"http+unix://{self._q_socket}{path}",
            data=json.dumps(data), headers={"Content-Type": "application/json"},
            timeout=BubbleAPI.timeout)
        if resp.ok:
            return self._handle_response_generated_exception(resp.json())
        raise Exception(resp.text)

    def _delete(self, path:str, data):
        if self.state!=BubbleState.RUNNING:
            # bubble is not running, there is nothing to do
            return None
        self._common_check(path)

        try:
            resp=self._session.delete(f"http+unix://{self._q_socket}{path}",
                data=json.dumps(data), headers={"Content-Type": "application/json"},
                timeout=BubbleAPI.timeout)
            if resp.ok:
                return self._handle_response_generated_exception(resp.json())
            raise Exception(resp.text)
        except requests.exceptions.ConnectionError:
            # bubble is not running, there is nothing to do
            pass

    def _handle_response_generated_exception(self, data):
        if data is None:
            return None
        if isinstance(data, dict):
            exp=data.get("exception")
            if exp is not None:
                raise Exception(exp)
        return data

    @property
    def state(self) -> BubbleState:
        """Get the state of the bubble
        Returns the same information as the Bubble.state property
        """
        if os.path.exists(self._socket):
            return BubbleState.RUNNING
        if os.path.exists(f"{self._socket}.terminated"):
            return BubbleState.TERMINATED
        return BubbleState.INITIALIZED

    @property
    def ready(self) -> bool:
        """Tells of the bubble is up and running"""
        if not os.path.exists(self._socket):
            return False
        try:
            self._get("/ping")
            return True
        except Exception as e:
            syslog.syslog(syslog.LOG_WARNING, f"could not determine if bubble is ready: {str(e)}")
            return False

    def wait_for_bubble_ready(self, max_delay=None):
        """Wait for the bubble to be set up
        If @max_delay is None, wait forever, otherwise after @may_delay (in miliseconds), an
        exception is raised
        """
        delay=10
        total=0
        if self.ready:
            return
        while True:
            time.sleep(delay/1000)
            total+=delay
            if max_delay is not None and total>max_delay:
                raise Exception("Bubble failed to be set up or could not connect to bubble")
            if self.ready:
                break

    def declare_env_variable(self, name:str, value:str|None):
        """Define an environment variable which all the future processes run in the bubble will have
        If @value is None (as opposed to the empty string ""), then the environment variable is actually
        removed if it existed.
        """
        try:
            self._post("/env", {"name": name, "value": value})
            return None
        except Exception as e:
            raise Exception(f"Failed to start process: {str(e)}")

    @property
    def environment(self) -> dict[str,str]:
        """Get the environment variable which all the future processes run in the bubble will have"""
        try:
            return self._get("/env") # pyright: ignore
        except Exception as e:
            raise Exception(f"Failed to get environment variables: {str(e)}")

    @property
    def auto_stop(self) -> bool:
        """Tells if the bubble destroys itself when the last managed process terminates"""
        try:
            return bool(self._get("/property", params={"name": "auto-stop"}))
        except Exception as e:
            raise Exception(f"Failed to get the 'auto-stop' property: {str(e)}")

    @auto_stop.setter
    def auto_stop(self, auto_stop:bool):
        """Defines if the bubble destroys itself when the last managed process terminates
        NB: this takes effect after processes terminations event, so setting auto_stop to True
        may still result in a bubble with no process right after this function is called
        """
        try:
            self._put("/property", {"name": "auto-stop", "value": auto_stop})
        except Exception as e:
            raise Exception(f"Failed to set the 'auto-stop' property to {auto_stop}: {str(e)}")

    def create_shared_tempory_directory(self) -> tuple[tempfile.TemporaryDirectory, str]:
        """Create a temporary directory which is visible both by processes running in the "host" and by processes in the bubble
        Returns a tuple containing:
        - a tempfile.TemporaryDirectory object
        - the name of that same directory as seen by the processes in the bubble
        """
        tmpdir=tempfile.TemporaryDirectory(dir=self._run_dir)
        return (tmpdir, "/bubble/run/"+os.path.basename(tmpdir.name))

    def start_process(self, args:list[str], ignore_status:bool=True, required:bool=False, extra_env:dict[str,str]|None=None,
        child_stdin:str|None=None, child_stdout_file:str|None=None, child_stderr_file:str|None=None,
        capabilities:str|None=None, restart:bool=False) -> int:
        """Start a process in the bubble
        The child_stdout_file and child_stderr_file can be used to specify how the started process's
        stdout and stderr are handled:
            - by default, they are discarded
            - a filename (or FIFO) can be passed to catch it. Use create_shared_tempory_directory() to get a temporary
            directory in which filenames visible both by processes in the host and in the bubble can be created

        To pass some data to the process as stdin, use the child_stdin argument which is interpreted as:
            - a file which contents is used as stdin (if it exists)
            - a simple string otherwise, passed directly
        Returns: the process's PID (in the PID namespace of the bubble)
        """
        try:
            data=self._post("/procs", {
                "args": args,
                "ignore-status": ignore_status,
                "required": required,
                "environ": extra_env,
                "child-stdin": child_stdin,
                "child-stdout": child_stdout_file,
                "child-stderr": child_stderr_file,
                "capabilities": capabilities,
                "restart": restart
            })
            return data["pid"] # pyright: ignore
        except Exception as e:
            raise Exception(f"Failed to start process: {str(e)}")

    def stop_process(self, pid:int):
        """Stop a process running in the bubble
        @pid is the PID of the process (in the PID namespace of the bubble)
        """
        try:
            self._delete("/procs", {"pid": pid})
        except Exception as e:
            err=str(e)
            if "PID not found" not in err and "No such process" not in err:
                raise Exception(f"Failed to stop process: {str(e)}")

    def suspend_process(self, pid:int):
        """Suspend a process in the bubble
        """
        try:
            self._put("/procs", {"pid": pid, "state": "suspend"})
        except Exception as e:
            raise Exception(f"Failed to suspend process: {str(e)}")

    def resume_process(self, pid:int):
        """Resume a suspended a process in the bubble
        """
        try:
            self._put("/procs", {"pid": pid, "state": "resume"})
        except Exception as e:
            raise Exception(f"Failed to resume process: {str(e)}")

    def get_processes(self, include_running:bool=True, include_terminated:bool=False) -> list[dict]:
        """List the managed processes in the bubble with at least the following attributes for each process:
            - pid
            - args: list[str]
            - state: str
        """
        try:
            params:dict[str,str]|None=None
            if include_running and not include_terminated:
                params={"state": "RUNNING"}
            elif not include_running and include_terminated:
                params={"state": "TERMINATED"}
            return self._get("/procs", params) # pyright: ignore
        except Exception as e:
            raise Exception(f"Failed to start process: {str(e)}")

    def get_process_exit_status(self, pid:int|None=None, wait:float=0) -> dict[int,int]|int|None:
        """If a process has terminated, get its exit status

        If @pid is None, returns a dictionary with the PID of each terminated process and
        its exit status.

        If @pid is not None:
        - returns the exit status if the process has terminated (128 + SIGNAL if killed by a signal)
        - returns None if the process is still running
        - raise an exception if the process does not exist
        - if @wait is >0, then wait up to that delay for the specified process to terminate
        """
        if pid is None:
            data={}
            for proc in self.get_processes(include_running=False, include_terminated=True):
                pid=proc.get("pid")
                if pid is None:
                    raise Exception("CODEBUG: get_processes() did not return a 'pid'")
                data[pid]=self.get_process_exit_status(pid)
            return data
        else:
            status=self._get(f"/proc/{pid}")
            if status is not None or wait<=0:
                return status

            waited=0
            wait_step=0.2
            while waited<wait:
                time.sleep(wait_step)
                waited+=wait_step
                status=self._get(f"/proc/{pid}")
                if status is not None:
                    return status
            raise ProcessNotYetTerminatedException("Process has not yet finished")

    def is_process_running(self, pid:int) -> bool:
        """Tell if a process is running or not
        Return False even for non existing processes.
        """
        if not pid:
            return False
        try:
            st=self.get_process_exit_status(pid)
            if st is not None:
                return False
            else:
                return True
        except Exception:
            return False

    def get_process_status(self, pid:int) -> int|None:
        """Get the status of a managed process (a process which has been started using start_process())
        """
        try:
            if _debug:
                syslog.syslog(syslog.LOG_DEBUG, f"Getting status of pid {pid}")
            pid=int(pid)
            return self._get(f"/proc/{pid}") # pyright: ignore
        except Exception as e:
            raise Exception(f"Failed to get process's status: {str(e)}")

    def get_vde_networking_status(self):
        """If VDE networking has been specified, get the network status"""
        try:
            return self._get("/vdenet")
        except Exception as e:
            raise Exception(f"Failed to get VDE networking status: {str(e)}")

    def activate_vde_networking(self):
        """If VDE networking has been specified, activate the networking features"""
        try:
            return self._put("/vdenet", {"active": True})
        except Exception as e:
            raise Exception(f"Failed to activate VDE networking: {str(e)}")

    def deactivate_vde_networking(self):
        """If VDE networking has been specified, activate the networking features"""
        try:
            return self._put("/vdenet", {"active": False})
        except Exception as e:
            raise Exception(f"Failed to deactivate VDE networking: {str(e)}")

    def get_vde_networking_allowed_ips(self):
        """Get the list of VDE IP addresses which are allowed by the bubble's internal firewall
        """
        try:
            return self._get("/netfilter")
        except Exception as e:
            raise Exception(f"Failed: {str(e)}")

    def define_vde_networking_allowed_ips(self, ips:list[str]):
        """Define the list of VDE IP addresses which are allowed by the bubble's internal firewall
        """
        try:
            if ips is None:
                ips=[]
            return self._put("/netfilter", {"ips": ips})
        except Exception as e:
            raise Exception(f"Failed: {str(e)}")
