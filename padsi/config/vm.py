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

import enum
import grp
import json
import os
import pwd
import re
import syslog

import nsbubble

from .mountpoint import MountPoint
from .network import NetworkRessources, NetworkSpec
from .proxy import Proxy

_debug = False


class VMUsage(str, enum.Enum):
    INSTALL = "install"
    UPDATE = "update"
    RUN = "run"


class VMScript(str, enum.Enum):
    UPDATE = "update"
    CUSTOMIZE = "customize"
    RUN = "run"
    SHUTDOWN = "shutdown"


class VirtualMachine:
    """Represent a VM configuration (not a running VM instance)"""

    def __init__(self, vm_id: str, os_variant: str, os_version: str | None, vm_descr: str, vm_dir: str, usage: VMUsage, specs: nsbubble.VMSpecs,
        show_ui: bool, read_only: bool, mounts: list[MountPoint], network: NetworkSpec|None, allowed_users: list[str]|None, scripts: dict[VMScript, str]):
        """NB: the zone argument is used to copy the resolution and firewall rules from the zone itself"""
        if vm_dir is None:
            raise Exception(f"Invalid VM directory{vm_dir}")
        self._vm_dir: str = vm_dir
        self._os_variant: str = os_variant
        self._os_version: str|None = os_version
        self._descr: str = vm_descr
        self._id: str = vm_id
        self._usage: VMUsage = usage
        self._vm_specs: nsbubble.VMSpecs = specs
        self._show_ui = show_ui
        self._read_only = read_only
        self._scripts: dict[VMScript, str] = scripts
        self._mounts: list[MountPoint] = mounts if mounts else []
        self._network: NetworkSpec|None = network
        self._allowed_users: list[str]|None = allowed_users
        if allowed_users is not None:
            for allowed in allowed_users:
                if not isinstance(allowed, str) or not re.match(r'^%?[_a-z][-0-9_a-z\.]*\$?$', allowed):
                    raise Exception(f"Invalid allowed user '{allowed}'")

    def __repr__(self):
        return f"VirtualMachine {self._id}/{self._usage}"

    def specialize(self, data: dict, named_netres: dict[str, NetworkRessources] | None) -> VirtualMachine:
        """Create a new VirtualMachine object as a specialization of the current VM"""
        # VM specifications overrides
        show_ui = self._show_ui
        read_only = self._read_only
        sect = data.get("specs")
        if sect is not None:
            mem_mb = sect.get("mem-mb")
            nb_cpu = sect.get("nb-cpu")
            disk_size_mb = sect.get("disk-size-mb")
            read_only = sect.get("read-only")
            show_ui = sect.get("show-ui")
            vm_specs = nsbubble.VMSpecs(
                disk_size_mb=self._vm_specs.disk_size_mb
                if disk_size_mb is None
                else disk_size_mb,
                mem_mb=self._vm_specs.mem_mb if mem_mb is None else mem_mb,
                nb_cpu=self._vm_specs.nb_cpu if nb_cpu is None else nb_cpu,
                net_type=self._vm_specs.net_type,
                graphical_device=True,
                secure_boot=self._vm_specs.secure_boot,
            )
        else:
            vm_specs = self._vm_specs

        # network overrides
        sect = data.get("network")
        if sect is not None:
            if self._network is None:
                network = NetworkSpec.from_data(sect, named_netres)
            else:
                network = self._network.specialize(sect, named_netres)
        else:
            network = self._network

        # new object
        return VirtualMachine(
            vm_id=self._id,
            os_variant=self._os_variant,
            os_version=self._os_version,
            vm_descr=self._descr,
            vm_dir=self._vm_dir,
            usage=self._usage,
            specs=vm_specs,
            show_ui=self.show_ui if show_ui is None else show_ui,
            read_only=self.read_only if read_only is None else read_only,
            mounts=self._mounts,
            network=network,
            allowed_users=self._allowed_users,
            scripts=self._scripts,
        )

    @property
    def directory(self) -> str:
        """The path in which all the VM's associated files are (cf. VM files' lifecycle)"""
        return self._vm_dir

    @property
    def description(self) -> str:
        return self._descr

    @property
    def os_variant(self) -> str:
        """Variant of the OS, like "linux" or "windows" """
        return self._os_variant

    @property
    def os_version(self) -> str|None:
        """Version of the OS, like "debian" or "debian/12" """
        return self._os_version

    @property
    def id(self) -> str:
        return self._id

    @property
    def usage(self) -> VMUsage:
        return self._usage

    @property
    def specs(self) -> nsbubble.VMSpecs:
        return self._vm_specs

    @property
    def show_ui(self) -> bool:
        return self._show_ui

    @property
    def read_only(self) -> bool:
        return self._read_only

    @property
    def mount_points(self) -> list[MountPoint]:
        """Get the list of mount points configured in the VM, with regards to the zone's configuration"""
        return self._mounts

    @property
    def network(self) -> NetworkSpec | None:
        return self._network

    def is_network_compatible(self, other_network: NetworkSpec|None, proxies:list[Proxy]) -> tuple[bool, str|None]:
        """Tell if the network requirements for the VM are allowed by the
        specified network specifications
        """
        if self._network is None or self._network.resolv_rules is None:
            return (True, None)
        if other_network is None or other_network.resolv_rules is None:
            return (False, "no network access or no DNS resolution")

        # resolv. rules
        for rule in self._network.resolv_rules:
            if rule.action != "allow":
                # ignore a deny rule as it is not a networking requirement
                continue

            allowed = False
            for orule in other_network.resolv_rules:
                if orule.action == "allow" and rule.is_part_of(orule.endpoint):
                    allowed = True
                    # print(f"    rule {rule.endpoint} is a subset of {orule.endpoint}")
                    break

            if not allowed:
                # check if access via proxies is enabled
                for proxy in proxies:
                    if proxy.resolv_rules is not None:
                        for prule in proxy.resolv_rules:
                            if prule.action == "allow" and rule.is_part_of(prule.endpoint):
                                allowed = True
                                #print(f"    rule {rule.endpoint} is a subset of {prule.endpoint} (proxy '{proxy.descr}')")
                                break
                    if allowed:
                        break

            if not allowed:
                # print(f"    rule not allowed: {rule.endpoint}")
                return (False, str(rule.endpoint))
        return (True, None)

    def is_user_allowed(self, uid: int) -> bool:
        """Determine if user is allowed to use the VM (for the associated usage)"""
        if uid == 0:
            return True # root is always allowed
        if self._allowed_users is None:
            return True

        # check if user is "padsi", always allowed
        try:
            if pwd.getpwnam("padsi").pw_uid==uid:
                return True
        except Exception:
            syslog.syslog(syslog.LOG_WARNING, "It seems the 'padsi' user is not present in the system")

        # get username and quick check
        try:
            username = pwd.getpwuid(uid).pw_name
        except Exception as e:
            raise e
        if self._allowed_users is None or username in self._allowed_users:
            return True

        # use groups
        for allowed in self._allowed_users:
            if allowed[0]=="%":
                try:
                    group=grp.getgrnam(allowed[1:])
                    if username in group.gr_mem:
                        return True
                except KeyError:
                    syslog.syslog(syslog.LOG_WARNING, f"Referenced group '{allowed[1:]}' does not exist")

        return False

    def check_user_allowed(self, uid: int):
        """Check the user is allowed to use the VM (for the associated usage)
        and raise an exception if not
        """
        if not self.is_user_allowed(uid):
            raise Exception(f"User with UID {uid} is not allowed to {self.usage.value.lower()} VM '{self._id}'")

    def get_script(self, script_usage: VMScript) -> str | None:
        """Get the name of the script to be executed by the PADSI agent"""
        return self._scripts.get(script_usage)


def load_vm_file(path: str, root_path: str, named_netres: dict[str, NetworkRessources] | None) -> tuple[str, dict[VMUsage, VirtualMachine | None]]:
    """Load the contents of a .vm file as a dictionary indexed by VMUsage"""
    with open(path, "r") as fd:
        data = json.load(fd)
        try:
            # specifications section
            specdata = data.get("specs")
            descr = specdata.get("descr")
            variant_full = specdata.get("variant")
            os_variant=None
            if "/" in variant_full:
                (os_variant, os_version) = variant_full.split("/", maxsplit=1)
            else:
                os_version = None
            if os_variant is None:
                raise Exception("OS variant is not specified")
            if os_variant not in ("linux", "windows"):
                raise Exception(f"Unhandled '{os_variant}' OS variant")

            vm_dir = os.path.basename(path)
            (vm_dir, *_) = vm_dir.split(".", maxsplit=1)  # remove any file extension (normally ".vm")
            if not re.match("^[a-zA-Z0-9-_]", vm_dir):
                raise Exception(f"Invalid VM name '{vm_dir}'")

            if not os.path.isabs(vm_dir):
                # VM files are by default in /var/padsi/VM
                vm_dir = os.path.join(root_path, "VM", vm_dir)
            mem_mb = specdata.get("mem-mb", 1024)
            nb_cpu = specdata.get("nb-cpu", 1)
            secure_boot = specdata.get("secure-boot", True)
            disk_size_mb = specdata.get("disk-size-mb", 20000)

            # admin section
            admindata = data.get("admin")
            if admindata is None:
                raise Exception("No 'admin' section")

            scripts: dict[VMScript, str] = {}
            for su in VMScript:
                script = admindata.get(f"{su.value}-script")
                if script is not None:
                    if not isinstance(script, str):
                        raise Exception(f"Invalid script path '{script}'")
                    scripts[su] = script

        except Exception as e:
            raise Exception(f"Invalid definition for VM '{path}': {str(e)}")

        # compute and validate vm_id
        vm_id = os.path.basename(path)[:-3]  # file name less the ".vm" extension
        if not re.match(r"^[a-z][a-z0-9]+", vm_id):
            raise Exception(f"invalid VM name '{vm_id}'")

        res = {}
        for usage in VMUsage:
            usagedata = data.get(usage.value)
            if usagedata is not None:
                with_net = usagedata.get("with-net", True)
                specs = nsbubble.VMSpecs(
                    mem_mb=mem_mb,
                    nb_cpu=nb_cpu,
                    disk_size_mb=disk_size_mb,
                    net_type=None if not with_net else "tap:tapvm",
                    secure_boot=secure_boot,
                )

                # mount points
                if usage == VMUsage.RUN:
                    mounts = MountPoint.load_from_data(usagedata.get("mounts"), allow_absolute_destination_path=False)
                    if mounts is None:
                        mounts=[]
                else:
                    if usagedata.get("mounts"):
                        raise Exception(f"Mount points are not allowed for the {usage.value} usage")
                    mounts = []

                # network
                net = NetworkSpec.from_data(usagedata.get("network"), named_netres, rules_only=True)

                vm_conf = VirtualMachine(
                    vm_id,
                    os_variant=os_variant,
                    os_version=os_version,
                    vm_descr=descr,
                    vm_dir=vm_dir,
                    usage=usage,
                    specs=specs,
                    show_ui=usagedata.get("show-ui", True),
                    read_only=usagedata.get("read-only", False),
                    scripts=scripts,
                    mounts=mounts,
                    network=net,
                    allowed_users=usagedata.get("allowed-users"),
                )
                res[usage] = vm_conf
            else:
                res[usage] = None
        return (vm_id, res)

def strip_vm_id(vm_id: str) -> str:
    if vm_id is None:
        raise Exception("virtual machine not specified")
    vm_id = vm_id.strip()
    if vm_id == "":
        raise Exception("invalid empty VM ID")
    return vm_id
