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

from __future__ import annotations

import os
import syslog
import uuid

import nsbubble
import padsi.config


def _get_host_dbus_socket_path() -> str:
    if "INVOCATION_ID" in os.environ:
        # we are being run by systemd => get the information from the gnome-shell itself:
        # os.geteiud() -> /run/user/<uid> -> look for the DBus socket
        path=os.path.join("/run", "user", str(os.geteuid()), "bus")
        if not os.path.exists(path):
            raise Exception(f"Expected DBus server socket '{path}' does not exist")
    else:
        dbus_env=os.environ.get("DBUS_SESSION_BUS_ADDRESS")
        if not dbus_env: # will be like "unix:path=/run/user/1000/bus"
            raise Exception(f"The DBUS_SESSION_BUS_ADDRESS environment variable is not defined, env:{os.environ}")
        (_, path)=dbus_env.split("=")
        if not os.path.exists(path):
            raise Exception(f"DBus socket '{path}' does not exist")
    return path

class ZoneDBusRouter:
    """Object to set up the mount namespace in which a DBus router proxy will run
    for the zone"""
    def __init__(self, zone:padsi.config.Zone, logs_dir:str, options:list[padsi.config.ZoneOption], zone_dbus_socket_path:str, dbus_router_socket_path:str, run_dir:str):
        """Notes:
        - the host's session DBUS socket path is determined automatically
        - the zone_dbus_socket_path is the socket of the zone in the mount namespace of the namespace of the host
        - the dbus_router_socket_path is the socket which will be created by the DBus router service (n the mount namespace of the host)
        """
        self._zone_config=zone
        self._options=options
        self._logs_dir=logs_dir
        self._zone_dbus_socket_path=zone_dbus_socket_path
        self._host_dbus_socket_path=_get_host_dbus_socket_path()
        self._dbus_router_socket_path=dbus_router_socket_path
        self._bubble:nsbubble.Bubble|None=None
        self._dbus_router_host_pid:int|None=None # PID of the proxy in the host namespace

        self._dbus_router_path=os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__)))), "bin", "dbus-router")
        if not os.path.isfile(self._dbus_router_path):
            raise Exception(f"DBus router {self._dbus_router_path} is missing")

        znid=str(uuid.uuid4()) # to differentiate a zone's wayland proxy from another for the same user
        self._run_dir=f"{run_dir}/dbus-router-zone-{znid}"
        os.makedirs(self._run_dir, exist_ok=True)

    def destroy(self):
        if self._bubble is not None:
            self._bubble.destroy()
            self._bubble=None

    @property
    def ready(self):
        """Tell if the zone is ready to be used
        """
        if self._bubble is None:
            return False
        api=nsbubble.BubbleAPI(self._run_dir)
        return api.ready

    @property
    def run_dir(self) -> str:
        """Directory under which all the files specific to the bubble are located"""
        return self._run_dir

    @property
    def bubble_init_pid(self) -> int|None:
        return self._bubble.init_pid if self._bubble is not None else None

    @property
    def socket(self):
        """Get the name of the DBus server's socket"""
        return self._dbus_router_socket_path

    @property
    def mnt_namespace(self) -> str|None:
        """Get the mount namespace of the zone"""
        return self._bubble.mnt_namespace if self._bubble is not None else None

    @property
    def net_namespace(self) -> str|None:
        """Get the net namespace of the zone"""
        return self._bubble.net_namespace if self._bubble is not None else None

    def setup(self):
        # prepare mount points
        socket_path_dir=os.path.realpath(os.path.dirname(self._dbus_router_socket_path))
        os.makedirs(socket_path_dir, exist_ok=True)
        socket_path_fname=os.path.basename(self._dbus_router_socket_path)
        mounts={
            self._dbus_router_path: { # dbus-router program itself
                "mount-point": "/host/dbus-router",
                "read-only": True,
                "monitored": False
            },
            self._logs_dir: {
                "mount-point": "/var/log",
                "read-only": False,
                "monitored": False
            },
            socket_path_dir: {
                "mount-point": "/bubble/run/router", # where the socket created by the wayland proxy will be
                "read-only": False,
                "monitored": False
            },
            self._host_dbus_socket_path: {
                "mount-point": "/bubble/run/dbus-host.socket",
                "read-only": False,
                "monitored": False
            },
            self._zone_dbus_socket_path: {
                "mount-point": "/bubble/run/dbus-zone.socket",
                "read-only": False,
                "monitored": False
            }
        }

        syslog.syslog(syslog.LOG_DEBUG, f"Configuring DBus router for zone '{self._zone_config.name}'")
        features=nsbubble.Features(mounts=mounts, with_syslog=True)
        b=nsbubble.Bubble(features=features, run_dir=self._run_dir)
        b.setup()
        self._bubble=b
        syslog.syslog(syslog.LOG_DEBUG, f"Starting DBus router for zone {self._zone_config.name}: bubble started")

        api=nsbubble.BubbleAPI(self._run_dir)
        api.wait_for_bubble_ready(2000)

        args=["/host/dbus-router", "--logfile", "/var/log/dbus-router.log",
              "/bubble/run/dbus-host.socket", "/bubble/run/dbus-zone.socket", os.path.join("/bubble/run/router", socket_path_fname)]
        for option in self._options:
            if option.enabled and option.option_type==padsi.config.ZoneOptionType.SCREEN_SHARE:
                args+=["--screenshare"]
            elif option.enabled and option.option_type==padsi.config.ZoneOptionType.DESKTOP_NOTIFICATIONS:
                args+=["--notifications"]
        ppath=os.environ.get("PYTHONPATH")
        extra_env=None if ppath is None else {
            "PYTHONPATH": ppath
        }
        pid=api.start_process(args, extra_env=extra_env) # pyright: ignore
        counter=0
        while counter<10:
            counter+=1
            st=None
            try:
                st=api.get_process_exit_status(pid, 0.5)
            except nsbubble.ProcessNotYetTerminatedException:
                pass
            if st is not None:
                # process has stopped!
                raise Exception(f"DBus router for zone {self._zone_config.name} has stopped (status {st})")
            if os.path.exists(self.socket):
                self._dbus_router_host_pid=self._bubble.map_bubble_pid_to_host(pid)
                return
        raise Exception(f"DBus router for zone {self._zone_config.name} did not create its Unix socket file")
