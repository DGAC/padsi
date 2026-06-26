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

import ipaddress
import json
import logging
import os
import subprocess
import syslog

import padsi.misc
import padsi.network
import padsi.xdg

from .adminns import AdminNS, load_adminns_file
from .clipboard import ClipboardRule, Policy
from .mountpoint import MountPoint
from .network import (FWRule, NetworkRessources, load_netres_file,
                      load_rules_from_data)
from .trafficshaper import TrafficShaper, load_from_file
from .vm import VirtualMachine, VMUsage, load_vm_file
from .zone import StartMode, Zone, load_zone_file

_debug = False

# for the bridge network contained in each zone's network namespace
users_br_network = ipaddress.IPv4Network("192.168.0.0/16")
admin_br_network = ipaddress.IPv4Network("192.168.128.0/24")

# IP address of the VM from the zone's TAP interface
tap_ip = ipaddress.IPv4Address("192.168.244.1")

# IP address of the VM as seen from the VM
vm_ip = ipaddress.IPv4Address("192.168.244.2")

class Configuration:
    """Contains the complete PADSI configurations (networking environment and zones)"""

    def __init__(self, config_directory: str):
        self._config_dir = config_directory
        self._var_dir:str
        self._xdg_data_dirs: list[str] = []
        self._need_restart_check:str|None=None
        self._xdg_default_zone: str | None = None
        self._firewall_logs_group:int|None=None

        self._named_netres: dict[str, NetworkRessources] = {}
        self._zones: dict[str, Zone] = {} # all the zones, indexed by their name
        self._admin_ns_list: list[AdminNS] = [] # Admin NS
        self._vm_definitions: dict[VMUsage, dict[str, VirtualMachine]] = {}  # all the VM definitions, indexed by VMUsage and then by VM ID
        self._traffic_shapers: dict[str, TrafficShaper] = {}  # all the declared traffic shapers
        self._clipboard_rules:list[ClipboardRule]=[] # by default, allow copy/paste!

        fname: str|None = None
        host_in_rules_data = []
        host_out_rules_data = []
        try:
            # load the global configuration file
            fname = "padsi.conf"
            fpath = os.path.join(config_directory, fname)
            if not os.path.isfile(fpath):
                raise Exception("Global configuration file 'padsi.conf' is missing")
            with open(fpath, "r") as fd:
                data = json.load(fd)
                self._var_dir = data.get("var-dir")
                if not self._var_dir:
                    raise Exception("No 'var-dir' top level attribute in global configuration")
                self._xdg_default_zone = data.get("xdg-default-zone")

                # scripts section
                scripts=data.get("scripts")
                if scripts is not None:
                    if not isinstance(scripts, dict):
                        raise Exception("invalid 'scripts' section")
                    self._need_restart_check=scripts.get("need-restart-check")
                    if self._need_restart_check is not None and not os.path.exists(self._need_restart_check):
                        syslog.syslog(syslog.LOG_ERR, f"The 'need-restart-check' attribute points to a non existant file '{self._need_restart_check}'")

                # global network settings
                netdata = data.get("networking")
                if netdata is None:
                    raise Exception("Missing 'networking' section")
                cidr = netdata.get("host-network")
                if cidr is not None:
                    self._host_network = ipaddress.IPv4Network(cidr)
                else:
                    self._host_network = ipaddress.IPv4Network("10.202.0.0/16")  # safe default value

                host_access = netdata.get("host-access")
                if host_access is not None:
                    host_in_rules_data = host_access.get("in-rules")
                    host_out_rules_data = host_access.get("out-rules")

                # XDG data dirs
                datadirs = data.get("xdg-data-dirs", [])
                for dir in datadirs:
                    if isinstance(dir, str):
                        if os.path.exists(dir) and os.path.isabs(dir):
                            self._xdg_data_dirs.append(dir)
                        else:
                            syslog.syslog(syslog.LOG_INFO, f"xdg-data-dirs' directory '{dir}' does not exist or is invalid, ignoring")

                # apps which need to be runnable outside of any zone
                self._nozone_apps:list[str]|None=None
                section_data = data.get("nozone-apps")
                if section_data is not None:
                    nzlist = []
                    for nzapp in section_data:
                        nzapp = nzapp.strip()
                        if not isinstance(nzapp, str) or not nzapp:
                            raise Exception(f"Invalid app ID '{nzapp}' in the 'nozone-apps' section")
                        nzlist.append(nzapp)
                    self._nozone_apps = nzlist

                group=data.get("firewall-logs-group")
                if group is not None:
                    try:
                        self._firewall_logs_group=int(group)
                        if self._firewall_logs_group<0 or self._firewall_logs_group>=2**16:
                            raise Exception()
                    except Exception:
                        raise Exception(f"Invalid firewall logs group '{group}'")

                clipboard=data.get("clipboard")
                if clipboard is not None:
                    try:
                        for rule_data in clipboard:
                            crule=ClipboardRule.from_data(rule_data)
                            self._clipboard_rules.append(crule)
                    except Exception:
                        raise Exception(f"Invalid clipboard rules '{clipboard}'")

            # load all the traffic shapers' definitions
            for fname in os.listdir(config_directory):
                if fname.endswith(".tsp"):
                    tsp=load_from_file(os.path.join(config_directory, fname))
                    if tsp.name=="init":
                        raise Exception("Invalid traffic shaper name 'init'")
                    self._traffic_shapers[tsp.name]=tsp
            self._traffic_shapers={k: v for k, v in sorted(self._traffic_shapers.items(), key=lambda item: item[0])}

            # load all the network definitions
            for fname in os.listdir(config_directory):
                if fname.endswith(".netres"):
                    self._named_netres.update(load_netres_file(os.path.join(config_directory, fname)))

            # load all VM definitions
            for fname in os.listdir(config_directory):
                if fname.endswith(".vm"):
                    (vmid, vms) = load_vm_file(
                        os.path.join(config_directory, fname),
                        self._var_dir,
                        self._named_netres,
                    )
                    for usage in VMUsage:
                        if usage not in self._vm_definitions:
                            self._vm_definitions[usage] = {}
                        vm=vms[usage]
                        if vm is not None:
                            self._vm_definitions[usage][vmid] = vm

            # load all Zones definitions
            for fname in os.listdir(config_directory):
                if fname.endswith(".zone"):
                    zo = load_zone_file(
                        config_directory,
                        fname,
                        self._named_netres,
                        self._traffic_shapers,
                        self._vm_definitions[VMUsage.RUN],
                    )
                    self._zones[zo.name] = zo
                    if zo.name == self._xdg_default_zone:
                        # force the zone to always be present
                        zo.start_mode = StartMode.ALWAYS

            # load all Admin NS definitions
            for fname in os.listdir(config_directory):
                if fname.endswith(".admns"):
                    admin = load_adminns_file(
                        config_directory,
                        fname,
                        self._named_netres,
                        self._traffic_shapers
                    )
                    self._admin_ns_list.append(admin)

        except Exception as e:
            if fname is None:
                raise Exception(f"Failed to load configuration in '{config_directory}': {str(e)}")
            raise Exception(f"Failed to load configuration in '{config_directory}' (file '{fname}'): {str(e)}")

        # load host in and out rules
        (fw_rules, resolv_rules) = load_rules_from_data(host_in_rules_data, self._named_netres)
        if resolv_rules:
            raise Exception(f"Host network access for inbound connections does not allow non CIDR based rules {resolv_rules}")
        self._host_in_fw_rules = fw_rules
        (fw_rules, resolv_rules) = load_rules_from_data(host_out_rules_data, self._named_netres)
        if resolv_rules:
            raise Exception(f"Host network access for outbound connections does not allow non CIDR based rules {resolv_rules}")
        self._host_out_fw_rules = fw_rules

        # global coherence checks
        if self._xdg_default_zone is not None and self._xdg_default_zone not in self._zones:
            raise Exception(f"XDG default zone '{self._xdg_default_zone}' is not defined")
        if self._host_network is not None:
            if self._host_network.overlaps(users_br_network) or users_br_network.overlaps(self._host_network):
                raise Exception(f"Network configuration error: networks {str(users_br_network)} and {str(self._host_network)} overlap")
            try:
                if self._host_network.num_addresses<256:
                    raise Exception()
            except Exception:
                raise Exception(f"Network {str(self._host_network)} is too small")

        # check there is not duplicate incoming traffic rules
        all_in_rules:list[FWRule]=self._host_in_fw_rules if self._host_in_fw_rules else []
        for adminns in self._admin_ns_list:
            if adminns.traffic_shaper is None and adminns.in_fw_rules:
                for rule in adminns.in_fw_rules:
                    if rule in all_in_rules:
                        raise Exception(f"Duplicate inbound rule {rule.action} {rule.endpoint} ({rule.descr}) in the admin NS '{adminns.name}'")
                    all_in_rules.append(rule)
        self._admin_ns_list.sort(key=lambda x: x.name)

        # reserve some networks for the admin NS
        self._admin_sub_networks:list[ipaddress.IPv4Network]=[]
        adminns_len=len(self.admin_ns_list)
        if adminns_len>0:
            self._admin_sub_networks=[n for n in self._host_network.subnets(new_prefix=30)][-adminns_len::]
        self._admin_sub_network_index=0 # used networks
        self._host_network_generator = self._host_network.subnets(new_prefix=30)

        # check zone's mounts references
        all_vars = {item: item for item in padsi.misc.xdg_dirs}
        for name in self.get_zones_names():
            for xdg_dir in padsi.misc.xdg_dirs:
                key = f"{name}_{xdg_dir}"
                all_vars[key] = "Ok"  # we don't care about the value

        def _check_mountpoint(mp: MountPoint, context: str):
            try:
                padsi.misc.expand_variables_in_string(mp.mount_path, all_vars)
            except Exception:
                raise Exception(f"Invalid mount point in {context}: destination path '{mp.mount_path}' uses invalid variable")
            try:
                padsi.misc.expand_variables_in_string(mp.source_path, all_vars)
            except Exception:
                raise Exception(f"Invalid mount point in {context}: source path '{mp.source_path}' uses invalid variable")

        for item in self.zones:
            if item.mount_points is not None:
                for mp in item.mount_points:
                    _check_mountpoint(mp, f"zone '{item.name}'")
        if self._vm_definitions[VMUsage.RUN] is not None:
            for _, item in self._vm_definitions[VMUsage.RUN].items():
                if item.mount_points is not None:
                    for mp in item.mount_points:
                        _check_mountpoint(mp, f"VM '{item.id}'")

        # check clipboard rules
        self._clipboard_allowed_copy_from:dict[str,set[str]]={} # for a zone, list of zones where data can be copied from (i.e. pasted into)
        self._clipboard_allowed_paste_to:dict[str,set[str]]={} # for a zone, list of zones where data can be pasted to (i.e. copied from)
        zone_names=list(self._zones.keys())
        if len(self._clipboard_rules)>0:
            for crule in self._clipboard_rules:
                crule.check_zones_exist(zone_names)

            all_zones=self._zones.keys()
            for paste_zone in all_zones:
                pzlist:set[str]={paste_zone} # a zone can copy/paste with itself
                for zone_name in all_zones:
                    handled=False
                    for crule in self._clipboard_rules:
                        policy=crule.get_policy(zone_name, paste_zone)
                        if policy==Policy.DENY:
                            handled=True
                            break
                        elif policy==Policy.ALLOW:
                            pzlist.add(zone_name)
                            handled=True
                            break
                    if not handled:
                        # default is to allow copy/paste between zones
                        pzlist.add(zone_name)
                self._clipboard_allowed_copy_from[paste_zone]=pzlist

            for copy_zone in all_zones:
                czlist:set[str]={copy_zone} # a zone can copy/paste with itself
                for zone_name in all_zones:
                    if copy_zone in self._clipboard_allowed_copy_from[zone_name]:
                        czlist.add(zone_name)
                self._clipboard_allowed_paste_to[copy_zone]=czlist

        # misc.
        self._xdg_res = None

    @property
    def config_dir(self) -> str:
        """Directory containing all the files of which the configuration is made"""
        return self._config_dir

    @property
    def var_dir(self) -> str:
        """Directory under which all PADSI's non temporary resources will be located (e.g. /var/padsi)"""
        return self._var_dir

    @property
    def need_restart_file(self) -> str:
        return "/run/padsi-needrestart"

    @property
    def need_restart_check(self) -> str|None:
        """Get the name of a program to run to determine if the PADSI service
        is allowed to restart"""
        return self._need_restart_check

    @property
    def logs_dir(self) -> str:
        """Directory where all the (non syslog) logs will be (e.g. /var/padsi/log)"""
        return os.path.join(self.var_dir, "log")

    @property
    def firewall_logs_group(self) -> int|None:
        """Returns a number >=0 if firewall logs should be sent through a netlink socket to the specified group """
        return self._firewall_logs_group

    @property
    def zones_infos_dir(self) -> str:
        return "/run/padsi/zones-infos"

    @property
    def all_users_zone_home_dir(self) -> str:
        """Directory under which all the users's HOME directories for all the zones will be"""
        return os.path.join(self.var_dir, "home")

    def get_zone_user_home_dir(self, uid: int, zone_name: str | None = None) -> str:
        """Get the directory where user's files will be stored for all the zones (if zone_name is None) or the
        specified zone otherwise
        """
        if zone_name is None:
            return os.path.join(self.all_users_zone_home_dir, str(uid))
        return os.path.join(self.all_users_zone_home_dir, str(uid), zone_name)

    """Directory below where all the users' runtime files will be stored for all the zones """
    all_users_run_dir = "/run/padsi/user"

    def get_user_run_dir(self, uid: int) -> str:
        """Get the directory where user's runtime files will be stored for all the zones,
        e.g. "/run/padsi/user/<UID>"
        """
        return os.path.join(self.__class__.all_users_run_dir, str(uid))

    def get_user_logs_dir(self, uid: int) -> str:
        """Get the directory where applications related to a user actually log,
        e.g. "/var/padsi/log/<UID>"
        """
        return os.path.join(self.logs_dir, str(uid))

    def get_zone_logs_dir(self, zone_name: str, uid: int):
        """Get the directory where zone related apps for a user actually log,
        e.g. "/var/padsi/log/<UID>/<zone_name>"
        """
        return os.path.join(self.logs_dir, str(uid), zone_name)

    def get_admin_ns_logs_dir(self, adminns_name: str):
        return os.path.join(self.logs_dir, "padsi", f"admns-{adminns_name}")

    @property
    def host_inbound_fw_rules(self) -> list[FWRule] | None:
        """List of rules for the host' inbound connections
        (the "init network namespace)
        """
        return self._host_in_fw_rules

    @property
    def host_outbound_fw_rules(self) -> list[FWRule] | None:
        """List of rules for the host' outbound connections
        (the "init network namespace)
        """
        return self._host_out_fw_rules

    @property
    def xdg_resources(self) -> padsi.xdg.XDGResources:
        if self._xdg_res is None:
            self._xdg_res = padsi.xdg.XDGResources(self.var_dir, self.xdg_data_dirs)
        return self._xdg_res

    @property
    def xdg_data_dirs(self) -> list[str]:
        return self._xdg_data_dirs

    @property
    def xdg_default_zone(self) -> Zone | None:
        """Zone in which all programs are run if there is no specific zone marking"""
        return (
            None
            if self._xdg_default_zone is None
            else self._zones.get(self._xdg_default_zone)
        )

    @property
    def traffic_shapers(self) -> list[TrafficShaper]:
        return list(self._traffic_shapers.values())

    def get_traffic_shaper(self, tsp_name: str) -> TrafficShaper | None:
        return self._traffic_shapers.get(tsp_name)

    @property
    def use_wayland_proxies(self) -> bool:
        """Tell if Wayland proxies need to be deployed in all the infra zones
        """
        return len(self._clipboard_rules)!=0

    def zone_needs_wayland_proxy(self, zone_name:str) -> bool:
        """Tell if a Wayland proxy needs to be present in the infrastructure of a zone
        """
        # a proxy is needed if pasting to the zone is somehow restricted
        all_zones=self._zones.keys()
        nb_zones=len(all_zones)
        if len(self._clipboard_allowed_copy_from[zone_name])!=nb_zones:
            return True

        # a proxy is not needed if there is no filtering or if the only filtering is that a single zone is completely "isolated"
        nbz:int|None=None
        for zn in all_zones:
            if zn!=zone_name:
                if nbz is None:
                    nbz=len(self._clipboard_allowed_paste_to[zone_name])
                    if nbz!=nb_zones and nbz!=nb_zones-1:
                        return True
                elif nbz!=len(self._clipboard_allowed_paste_to[zone_name]):
                    return True
        return False

    def get_clipboard_allowed_zones(self, paste_zone:str) -> set[str]:
        """Get the list of zones from which content can be copied and pasted into
        the specified zone
        """
        return self._clipboard_allowed_copy_from[paste_zone]

    @property
    def zones(self) -> list[Zone]:
        return list(self._zones.values())

    def get_zones_names(self) -> list[str]:
        return list(self._zones.keys())

    def get_zone(self, zone_name: str) -> Zone:
        """Get a zone configuration from its name.
        Raise an Exception if not found
        """
        zone = self._zones.get(zone_name)
        if zone is not None:
            return zone
        raise Exception(f"Undefined zone '{zone_name}'")

    @property
    def admin_ns_list(self) -> list[AdminNS]:
        return self._admin_ns_list

    def get_admin_ns_names(self) -> list[str]:
        return [admin.name for admin in self._admin_ns_list]

    def get_admin_ns(self, name:str) -> AdminNS:
        """Get an admin NS configuration from its name.
        Raise an Exception if not found
        """
        for admin in self._admin_ns_list:
            if admin.name==name:
                return admin
        raise Exception(f"Undefined admin NS '{name}'")

    def get_unused_low_network(self) -> ipaddress.IPv4Network:
        """Return au unused network which contains at least 2 ip addresses in its range, which can be used
        to route network traffic from a zone to the outside world (in the "init" net namespace or in a WireGuard
        dedicated net namespace)

        Example: 10.202.0.1/30
        """
        try:
            while True:
                net = next(self._host_network_generator)

                # check that the provided IP is not already assigned
                used = False

                if net in self._admin_sub_networks:
                    used=True
                else:
                    for addr in net:
                        if padsi.network.addr_exists(addr):
                            used = True
                            continue
                if not used:
                    if _debug:
                        syslog.syslog(syslog.LOG_DEBUG, f"consumed network {net}")
                    return net
        except StopIteration:
            pass
        msg = f"No more networks available in '{self._host_network}'"
        syslog.syslog(syslog.LOG_ERR, msg)
        raise Exception(msg)

    def get_unused_low_admin_network(self) -> ipaddress.IPv4Network:
        """Return au unused network which contains at least 2 ip addresses in its range, which can be used
        to route network traffic of an admin NS to the outside world (in the "init" net namespace or in a WireGuard
        dedicated net namespace)
        Similar to get_unused_low_network() but in a range dedicated to admin NS.

        Example: 10.202.255.249/30
        """
        if self._admin_sub_network_index==len(self._admin_sub_networks):
            msg = "No more admin networks available"
            syslog.syslog(syslog.LOG_ERR, msg)
            raise Exception(msg)
        net=self._admin_sub_networks[self._admin_sub_network_index]
        self._admin_sub_network_index+=1
        if _debug:
            syslog.syslog(syslog.LOG_DEBUG, f"consumed ADMIN network {net}")
        return net

    def get_vms_for_usage(self, usage: VMUsage) -> list[VirtualMachine]:
        """Get the VM definitions for the specified usage"""
        return list(self._vm_definitions[usage].values())

    def get_vm(self, usage: VMUsage, vm_id: str) -> VirtualMachine | None:
        """Get a VM definition for a specific usage and its ID"""
        try:
            return self._vm_definitions[usage][vm_id]
        except Exception:
            return None

    def XDG_DATA_DIRS_install(self):
        """Create the /etc/profile.d/zzz_padsi.sh file to globally set the XDG_DATA_DIRS variable"""
        # prepare all XDG data dirs actually used, one per item defined in the conf.
        vars: list[str] = [self.var_dir]
        for xdg_dir in self.xdg_data_dirs:
            xdg_dir = os.path.join(self.var_dir, "xdg", xdg_dir[1:])
            os.makedirs(os.path.join(xdg_dir, "applications"), exist_ok=True)
            vars.append(xdg_dir)
        padsi_vars = ":".join(vars)

        # update/overwrite the etc file
        padsi_source_dir = os.path.dirname(
            os.path.dirname(os.path.realpath(os.path.dirname(__file__)))
        )
        with open(os.path.join(padsi_source_dir, "etc", "profile.d", "zzz_padsi.sh.templ")) as fd:
            data = padsi.misc.expand_variables_in_string(fd.read(), {"padsi_vars": padsi_vars})
            if os.path.isdir("/etc/profile.d"):
                with open("/etc/profile.d/zzz_padsi.sh", "w") as fd:
                    fd.write(data)
            else:
                raise Exception("The '/etc/profile.d' directory does not exist on this system")

    def _get_xdg_dir_for_de_file(self, de_file: str) -> str:
        for xdg_dir in self.xdg_data_dirs:
            if de_file.startswith(xdg_dir):
                return xdg_dir[1:]  # remove leading "/"
        raise Exception(f"Desktop entry file/dir '{de_file}' is not in any of the declared XDG data directories")

    def get_de_files_install_dir(self, de_file: str | None, zone_name: str | None) -> str:
        """Determine where desktop entry files created by this object are installed. the de_file string may represent an actual dekstop
        entry file or a directory where the desktop entry files are stored.

        If user_zone_name is None, then the system wide directory is returned, else
        the user (running the program) specific directory is returned.

        Note: if de_file is None, then only the global to directory where _any_ desktop entry would be stored is returned
        """
        if zone_name is None:
            # global install
            if de_file is not None:
                xdg_dir = self._get_xdg_dir_for_de_file(de_file)
                install_dir = os.path.join(self.var_dir, "xdg", xdg_dir, "applications")  # like /var/padsi/xdg/usr/share/applications
            else:
                install_dir = os.path.join(self.var_dir, "xdg")
        else:
            install_dir = os.path.join(
                padsi.misc.get_user_home_dir(os.geteuid()),
                ".local",
                "share",
                "applications",
            )  # $HOME/.local/share/applications

        try:
            os.makedirs(install_dir, exist_ok=True)
        except PermissionError:
            raise Exception(f"CODEBUG: could not create desktop entries directory '{install_dir}' due to permissions")

        return install_dir

    def get_icons_install_dir(self, de_file: str | None, zone_name: str | None) -> str:
        """Determine where icon files created by this object are installed for the specified zone. If is_global is True, then
        the system wide directory is returned, else the user (running the program) specific directory is returned

        Note: if zone_name is None, then this method returns the directory containing a sub directory per zone name (which in turn contain icons)
        """
        if zone_name is None:
            # global install
            if de_file is not None:
                xdg_dir = self._get_xdg_dir_for_de_file(de_file)
                install_dir = os.path.join(
                    self.var_dir, "icons", xdg_dir
                )  # like /var/padsi/icons/usr/share
            else:
                install_dir = os.path.join(self.var_dir, "icons")
        else:
            install_dir = os.path.join(
                padsi.misc.get_user_home_dir(os.geteuid()),
                ".local",
                "share",
                "padsi-icons",
            )  # $HOME/.local/share/padsi-icons

        try:
            os.makedirs(install_dir, exist_ok=True)
        except PermissionError:
            raise Exception(f"CODEBUG: could not create icons directory '{install_dir}' due to permissions")

        return install_dir

    def get_already_created_desktop_entry_files(self, de_dir: str, user_zone_name: str | None) -> set[str]:
        """Build a list of all existing previously created files for desktop entry files in the specified directory.

        If user_zone_name is None, then the desktop entry file is considered to be system wide. Otherwise the desktop entry file is considered
        to be in the user's home directory of the specified zone
        """
        is_global = user_zone_name is None
        install_dir = self.get_de_files_install_dir(de_dir, user_zone_name)

        res: set[str] = set()
        prefix = f"padsi.{user_zone_name}." if not is_global else None
        for fname in os.listdir(install_dir):
            if is_global:
                res.add(os.path.join(install_dir, fname))
            elif prefix is not None and fname.startswith(prefix):
                res.add(os.path.join(install_dir, fname))
        return res

    def get_already_created_icon_files(self, de_dir: str, zone_name: str|None) -> set[str]:
        """Build a list of all existing previously created resource"""
        install_dir = self.get_icons_install_dir(de_dir, zone_name)
        res = set()
        for fname in os.listdir(install_dir):
            fpath = os.path.join(install_dir, fname)
            if os.path.isfile(fpath):
                if zone_name is None or fname.startswith(f"padsi.{zone_name}"):
                    res.add(fpath)
        return res

    def desktop_entry_install(self, de_file: str, user_zone_name: str | None) -> tuple[set[str], set[str]]:
        """Customize the desktop entry's icon and use symlinks to adapt the UX of the user to the PADSI's configuration
          and generate the desktop entries index (used to match executable files to a desktop entry).

        This customization allows to associate correct icons (launchers and in the Dock when running)
        to applications running in the various zones. The association relies on using App IDs (see the Wayland protocol)
        in the form "padsi.<zone name>.<App ID>":
        - by customizing all the .desktop desktop entry files
        - by deriving applications' icons to make them visually customized to the zone's color
        - by having the Wayland proxy modify on the fly the App ID sent by each application

        However, for the zone marked as the "xdg-default-zone" in the global configuration, there is
        no visual indication (as this is the default zone the user is expected to use). If such a
        XDG default zone is defined, then all the existing desktop entries are modified and marked as
        NoDisplay=true, and a PadsiOriginalNoDisplay key is added to hold the "real" NoDisplay value of
        the entry and be able to restore when restore_desktop is called

        If user_zone_name is None, then the desktop entry file is considered to be system wide. Otherwise the desktop entry file is considered
        to be in the zone's $HOME/.local/share/applications directory

        Returns a tuple containing the sets of created/updated desktop entry files and icon files
        """
        is_global = user_zone_name is None

        de_install_dir = self.get_de_files_install_dir(de_file, user_zone_name)
        icons_install_dir = self.get_icons_install_dir(de_file, user_zone_name)

        touched_de_files: set[str] = set()
        touched_icon_files: set[str] = set()

        if de_file.endswith(".desktop") and not de_file.startswith("padsi."):
            de = padsi.xdg.DesktopEntry(de_file, xdg_res=self.xdg_resources)
            # if de.app_id not in ("org.gnome.Calculator", "org.gnome.TextEditor", "org.gnome.Terminal", "org.gnome.Nautilus", "firefox-esr"):
            #    continue
            ignore=False
            if not de.app_id.startswith("padsi-vm-viewer") and (self._nozone_apps is None or de.app_id in self._nozone_apps):
                ignore=True # application supposed to be run in the "init" namespace => do nothing.
            try:
                if not ignore:
                    if is_global:
                        zones=self._zones
                    else:
                        uzone=self._zones.get(user_zone_name)
                        if uzone is None:
                            zones={}
                        else:
                            zones={user_zone_name: uzone}

                    for zone_name, zone in zones.items():
                        if is_global and de.app_id in zone.apps or not is_global:
                            if zone == self.xdg_default_zone:
                                if _debug:
                                    logging.debug(f"In zone {zone_name} (default zone), customizing with icon_color=None, user_de={not is_global}, icons_install_dir={icons_install_dir}")
                                (de_files, icon_files) = de.customize_for_zone(
                                    zone=zone,
                                    de_install_dir=de_install_dir,
                                    icons_install_dir=icons_install_dir,
                                    icon_color=None,
                                    nodisplay=False,
                                    user_de=not is_global,
                                )
                            else:
                                if _debug:
                                    logging.debug(f"In zone {zone_name}, customizing with user_de={not is_global}, icons_install_dir={icons_install_dir}")
                                (de_files, icon_files) = de.customize_for_zone(
                                    zone=zone,
                                    de_install_dir=de_install_dir,
                                    icons_install_dir=icons_install_dir,
                                    icon_color=zone.color_str,
                                    nodisplay=False,
                                    user_de=not is_global,
                                )
                            touched_de_files.update(de_files)
                            touched_icon_files.update(icon_files)

                    if is_global:
                        # "mask" the application to be started in the "init" namespace for system-wide setup
                        res = subprocess.run([
                                "desktop-file-install",
                                "--dir",
                                de_install_dir,
                                de.filename,
                            ],
                            capture_output=True,
                            text=True
                        )
                        if res.returncode != 0:
                            syslog.syslog(syslog.LOG_WARNING, f"Could not copy app {de.app_id} original DE to {de_install_dir}: {res.stdout}, {res.stderr}")
                        else:
                            new_de_file = os.path.join(de_install_dir, os.path.basename(de.filename))
                            res = subprocess.run(
                                [
                                    "desktop-file-edit",
                                    "--set-key",
                                    "NoDisplay",
                                    "--set-value",
                                    "true",
                                    new_de_file,
                                ],
                                capture_output=True,
                                text=True
                            )
                            if res.returncode != 0:
                                raise Exception(f"Could not set Nodisplay in DE file to {new_de_file}: {res.stderr}")
                            touched_de_files.add(new_de_file)

            except Exception as e:
                syslog.syslog(syslog.LOG_ERR, f"Could not handle Desktop entry file '{de.app_id}': {str(e)}")
        return (touched_de_files, touched_icon_files)

    def desktop_entry_uninstall(self, de_file: str, user_zone_name: str | None) -> tuple[set[str], set[str]]:
        """Remove any desktop entry file related to the specified desketop entry file: files which were created using
        the corresponding desktop_entry_install() call.

        Returns a tuple containing the sets of removed desktop entry files and icon files
        """
        is_global = user_zone_name is None

        de_install_dir = self.get_de_files_install_dir(de_file, user_zone_name)
        icons_install_dir = self.get_icons_install_dir(de_file, user_zone_name)

        touched_de_files: set[str] = set()
        touched_icon_files: set[str] = set()

        if de_file.endswith(".desktop") and not de_file.startswith("padsi."):
            zones = (
                self._zones
                if is_global
                else {user_zone_name: self._zones.get(user_zone_name)}
            )
            suffix = os.path.basename(de_file)
            for fname in os.listdir(de_install_dir):
                if fname.startswith("padsi.") and fname.endswith(suffix):
                    (_, fname_zone, *_) = fname.split(".")
                    for zone_name, zone in zones.items():
                        if fname_zone == zone_name:
                            rem_file = os.path.join(de_install_dir, fname)

                            # remove icon if possible
                            de = padsi.xdg.DesktopEntry(rem_file, self.xdg_resources)
                            if de.icon_file is not None and de.icon_file.startswith(
                                icons_install_dir
                            ):
                                touched_icon_files.add(de.icon_file)
                                try:
                                    os.remove(de.icon_file)
                                except Exception as e:
                                    syslog.syslog(
                                        syslog.LOG_ERR,
                                        f"Could not remove obsolete icon file '{rem_file}': {str(e)}",
                                    )

                            # remove desktop entry file itself
                            touched_de_files.add(rem_file)
                            try:
                                os.remove(rem_file)
                            except Exception as e:
                                syslog.syslog(
                                    syslog.LOG_ERR,
                                    f"Could not remove obsolete desktop entry file '{rem_file}': {str(e)}",
                                )
        return (touched_de_files, touched_icon_files)
