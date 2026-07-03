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
# PADSI object to represent instanciated ("running") Zones
#

from __future__ import annotations

import hashlib
import os
import shutil
import syslog
import tempfile
import uuid

import nsbubble
import padsi.config

from .components import Component


class ComponentInstance:
    def __init__(self, comp: Component, auto_start:bool=True) -> None:
        self.component=comp
        self.auto_start=auto_start

class ZoneFoundations:
    """Common behaviour for all the "zones" (infra, apps, etc.)
    """
    def __init__(self, zone_type:str, global_config:padsi.config.Configuration,
        zone_conf:padsi.config.Zone|None, admin_conf:padsi.config.AdminNS|None,
        uid:int, run_dir:str, logs_dir:str
    ):
        if zone_conf is not None:
            if admin_conf is not None:
                raise Exception("CODEBUG: zone_conf and admin_ns should not be both specified")
            self._syslog_prefix=f"{zone_type}_{uid}_{zone_conf.name}"
        elif admin_conf is not None:
            self._syslog_prefix=f"{zone_type}_{uid}_{admin_conf.name}"
        else:
            raise Exception("CODEBUG: zone_conf and admin_ns should not be both None")

        self._z_type=zone_type
        self._gconf=global_config
        self._z_conf=zone_conf
        self._a_ns=admin_conf
        self._logs_dir=logs_dir
        os.makedirs(self._logs_dir, exist_ok=True)
        os.chmod(self._logs_dir, 0o700)
        self._uid=uid

        znid = str(uuid.uuid4())  # to differentiate a zone's run directories in the host
        self._run_dir = os.path.join(run_dir, f"{zone_type}-{znid}")
        os.makedirs(self._run_dir, exist_ok=True)
        os.chmod(self._run_dir, 0o700)

        self._tmpdir = tempfile.TemporaryDirectory()
        self._bubble=None
        self._api=None

        self._components: set[ComponentInstance]=set()
        self._mtu:int|None=None

    def __del__(self):
        self.stop()

    @property
    def global_conf(self) -> padsi.config.Configuration:
        """Global configuration"""
        return self._gconf

    @property
    def zone_conf(self) -> padsi.config.Zone:
        """Associated Zone configuration"""
        if self._z_conf is None:
            raise Exception("CODEBUG: zone_conf should not be None in ZoneFoundations")
        return self._z_conf

    @property
    def admin_conf(self) -> padsi.config.AdminNS:
        """Associated admin. NS configuration"""
        if self._a_ns is None:
            raise Exception("CODEBUG: admin_conf should not be None in ZoneFoundations")
        return self._a_ns

    @property
    def logs_dir(self) -> str:
        """Directory to store logs to, in the context of the "init" mount namespace
        """
        return self._logs_dir

    @property
    def logs_group(self) -> int|None:
        return self._gconf.firewall_logs_group

    @property
    def tmp_dir(self) -> str:
        """Temporary directory for the zone (in the context of the "init" mount namespace)
        """
        if self._tmpdir is None:
            raise Exception("CODEBUG: tmpdir not yet created")
        return self._tmpdir.name

    @property
    def uid(self) -> int:
        return self._uid

    @property
    def bubble(self) -> nsbubble.Bubble|None:
        return self._bubble

    @property
    def bubble_init_pid(self) -> int|None:
        return self._bubble.init_pid if self._bubble is not None else None

    @property
    def running_duration(self) -> int|None:
        """Get the running duration in seconds, or None if zone is not started
        """
        return None if self._bubble is None else self._bubble.running_duration

    @property
    def api(self) -> nsbubble.BubbleAPI|None:
        return self._api

    @property
    def run_dir(self) -> str:
        """Directory (in the context of the "init" mount namespace) under which all the files specific
        to the bubble are located"""
        return self._run_dir

    @property
    def mnt_namespace(self) -> str|None:
        """Get the mount namespace of the zone"""
        if self.ready:
            return self._bubble.mnt_namespace # pyright: ignore
        return None

    @property
    def net_namespace(self) -> str|None:
        """Get the net namespace of the zone"""
        if self.ready:
            return self._bubble.net_namespace # pyright: ignore
        return None

    @property
    def net_namespace_raw(self) -> str|None:
        return self._bubble.net_namespace_raw if self._bubble is not None else None

    @property
    def net_mtu(self) -> int|None:
        return self._mtu

    @net_mtu.setter
    def net_mtu(self, mtu:int|None):
        self._mtu=mtu

    @property
    def state(self) -> nsbubble.BubbleState|None:
        if self._bubble is None:
            return None
        return self._bubble.state

    @property
    def ready(self) -> bool:
        """Tell if the zone is ready to be used
        """
        return False if self._bubble is None or self._api is None else self._api.ready

    @property
    def env_variables(self) -> dict[str,str]:
        if self._api is None:
            raise Exception("Zone has not yet been started")
        return {} if self._api.environment is None else self._api.environment

    def add_component(self, comp:Component, auto_start:bool=True):
        """Declare a component used by the zone
        """
        self._components.add(ComponentInstance(comp, auto_start))

    def get_components(self) -> list[Component]:
        return [ci.component for ci in self._components]

    def start(self):
        """Actually start the bubble and the declared components
        """
        if self._bubble is not None:
            raise Exception(f"Zone {self._z_type} has already been started")

        # compute needed mount points, capabilities, users and groups
        mounts=self.compute_mount_points()
        caps:set[str]=set()
        users:set[str]=set()
        groups:set[str]=set()
        for icomp in self._components:
            c_mounts=icomp.component.get_mountpoints()
            if c_mounts is not None:
                mounts.update(c_mounts)
            c_caps=icomp.component.capabilities
            if c_caps is not None:
                for cap in c_caps:
                    caps.add(cap)
            c_user=icomp.component.get_required_user_entry()
            if c_user is not None:
                users.add(c_user)
            c_group=icomp.component.get_required_group_entry()
            if c_group is not None:
                groups.add(c_group)

        # bubble start
        features=self.features
        features.mounts=mounts
        features.capabilities=list(caps) if len(caps)>0 else None
        features.users=list(users) if len(users)>0 else None
        features.groups=list(groups) if len(groups)>0 else None

        b=self.create_bubble(features)
        b.setup()
        self._bubble=b

        api=nsbubble.BubbleAPI(self._run_dir)
        api.wait_for_bubble_ready(2000)
        self._api=api

        # start all components
        for icomp in self._components:
            if icomp.auto_start:
                try:
                    syslog.syslog(syslog.LOG_INFO, f"{self._syslog_prefix}: starting component '{icomp.component.name}'")
                    icomp.component.start(api)
                except Exception as e:
                    syslog.syslog(syslog.LOG_ERR, f"{self._syslog_prefix}: error starting component '{icomp.component.name}': {str(e)}")

    def stop(self):
        """Stop the bubble and all the components
        """
        if self._api is not None:
            for icomp in self._components:
                icomp.component.stop(self._api)
        if self._bubble is not None:
            self._bubble.destroy()
            self._bubble=None
            self._api=None
        if self._tmpdir is not None:
            self._tmpdir.cleanup()
            self._tmpdir = None
        if self._run_dir is not None:
            try:
                shutil.rmtree(self._run_dir)
            except FileNotFoundError:
                pass

    @property
    def processes(self) -> list[dict]:
        """List of managed processes, with at least the following attributes for each process:
            - pid
            - args: list[str]
            - state: str
        """
        if self._api is None:
            raise Exception("processes property: zone has not yet been started")
        return self._api.get_processes()

    @property
    def syslog_prefix(self) -> str:
        return self._syslog_prefix

    @syslog_prefix.setter
    def syslog_prefix(self, prefix:str):
        self._syslog_prefix=prefix


    #
    # methods which can be subclassed
    #
    def compute_mount_points(self) -> dict:
        """Get all the mount points for the zone, to be overridden by sub classes if necessary,
        not taking into account the mount points required by the components used
        """
        # load /etc/machine-id
        mid=""
        with open("/etc/machine-id", "rt") as fd:
            mid=fd.read().strip()
        if self._z_conf is not None:
            mid=hashlib.md5(f"{mid}-{self._z_conf.name}".encode()).hexdigest()[:32]

        mid_path=os.path.join(self.tmp_dir, "machine-id")
        with open(mid_path, "wt") as fd:
            fd.write(mid)
            fd.write("\n")

        return {
            mid_path: {
                "mount-point": "/etc/machine-id",
                "read-only": True,
                "monitored": False
            },
            self.logs_dir: {
                "mount-point": "/var/log",
                "read-only": False,
                "monitored": False
            }
        }

    @property
    def features(self) -> nsbubble.Features:
        raise Exception("The features() method must be overridden")

    def create_bubble(self, features:nsbubble.Features) -> nsbubble.Bubble:
        return nsbubble.Bubble(features=features, run_dir=self.run_dir)
