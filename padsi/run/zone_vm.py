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
# PADSI object to represent an instanciated ("running") zone containg a VM
#

from __future__ import annotations

import asyncio
import fcntl
import ipaddress
import os
import shutil
import signal
import syslog
import tempfile
import time

import firewall
import nsbubble
import padsi.config
from padsi.misc import (compute_user_xdg_subdirectories,
                        expand_variables_in_string)

from .components import dhcp, dns, fw_logger
from .components import static_firewall as stfw
from .components import usbredir, virtiofs
from .components import vm_monitor as monitor
from .components import web_infra
from .network_infra import external_zone_iface
from .vm.mgmtfiles import VMManagementFiles
from .vm.version import VMState, VMVersion
from .zone_apps import get_apps_generic_mount_points
from .zone_foundations import ZoneFoundations
from .zone_infra import ZoneInfra
from .zone_userfiles import ZoneUserFiles

_debug = True

class PadsiViewer(nsbubble.vm.Viewer):
    def __init__(self, vm_conf: padsi.config.VirtualMachine, padsi_install_dir: str, nickname: str|None):
        self._vm_conf = vm_conf
        self._nickname = nickname
        self._padsi_install_dir = padsi_install_dir
        self._app_id = f"padsi-vm-viewer.{self._vm_conf.id}.{self._nickname if self._nickname is not None else 'default'}"

    @property
    def needs_spice_socket(self) -> bool:
        # PADSI's viewer only needs the socket when the actual viewer widget is displayed
        return False

    @property
    def real_prog_name(self) -> str:
        return os.path.join(self._padsi_install_dir, "vm-management", "viewer", "padsi-vm-viewer")

    @property
    def bubble_prog_name(self) -> str:
        return os.path.join("/tmp", self._app_id)

    @property
    def env_variables(self) -> dict[str, str] | None:
        return { "PYTHONPATH": os.path.join(self._padsi_install_dir, "vm-management", "viewer") }

    def get_arguments(self, spice_socket_file: str) -> list[str]:
        os_info = (
            self._vm_conf.os_variant
            if self._vm_conf.os_version is None
            else f"{self._vm_conf.os_variant}/{self._vm_conf.os_version}"
        )
        options: list[str] = ["--appid", self._app_id]
        if self._nickname:
            options += ["--nickname", self._nickname]
        if not self._vm_conf.show_ui:
            options += ["--hide"]
        if self._vm_conf.read_only:
            options += ["--discard"]
        return [self.bubble_prog_name] + options + [
                spice_socket_file,
                self._vm_conf.description,
                self._vm_conf.usage.value,
                os_info
            ]

def _create_proxy(zone_conf:padsi.config.Zone, vm_conf: padsi.config.VirtualMachine, ip_address:ipaddress.IPv4Interface|None) -> padsi.config.Proxy|None:
    """Create a dedicated proxy conf. for the VM which reroutes to the zone's proxy and blocks denied traffic
    """
    if vm_conf.network is None or ip_address is None:
        return None

    next_hop=f"{ip_address.ip}:3128"
    fw_rules:list[padsi.config.FWRule]=[]
    if vm_conf.network.fw_rules is not None:
        fw_rules=[rule for rule in vm_conf.network.fw_rules if rule.action!="allow"]

    resolv_rules:list[padsi.config.ResolvRule]=[]
    if vm_conf.network.resolv_rules is not None:
        # only consider block rules, allow rules are controled by the zone in which the VM is running
        resolv_rules=[rule for rule in vm_conf.network.resolv_rules if rule.action!="allow"]
    # pass through, traffic will be filtered by the zone's proxy
    resolv_rules.append(padsi.config.ResolvRule("allow", None, firewall.Endpoint.from_repr("*")))

    return padsi.config.Proxy(next_hop, fw_rules, resolv_rules, "Proxy for zone's proxy")

class ZoneVM(ZoneFoundations):
    """Object to set up and configure a zone in which a (single) VM will run
    If the VM's usage is INSTALL or UPPDATE, then only the zone's network settings are used, not the zone' mount points
    """
    def __init__(self, global_conf: padsi.config.Configuration, zone_conf: padsi.config.Zone, uid: int, run_dir: str,
        logs_dir: str, zone_infra: ZoneInfra|None, zuf: ZoneUserFiles, vm_conf: padsi.config.VirtualMachine,
        vm_version: VMVersion, vmm: VMManagementFiles, ip_address: ipaddress.IPv4Interface|None, gid: int,
        boot_iso: str|None = None, extra_isos: list[str]|None = None, mtu: int|None = None):
        logs_vm_name=vm_version.nickname if vm_version.nickname is not None else str(vm_version).replace("/", "_")
        vm_logs_dir=os.path.join(logs_dir, f"VM-{vm_conf.id}", logs_vm_name)
        os.makedirs(vm_logs_dir, exist_ok=True)
        super().__init__("VM", global_conf, zone_conf, None,
            uid, run_dir, vm_logs_dir)
        self._z_infra=zone_infra
        self._zuf=zuf
        self.with_x11 = False # we want to use Wayland
        self._vm_conf = vm_conf
        self._gid = gid
        self._ip_address=ip_address
        self._boot_iso = boot_iso if vm_conf.usage == padsi.config.VMUsage.INSTALL else None
        self._extra_isos = extra_isos
        self._vm_v = vm_version
        self._vmm = vmm
        self._server_ssh_key_task: asyncio.Task|None = None
        self._domain_names:list[str]|None=None

        self._padsi_install_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        self._viewer = PadsiViewer(self._vm_conf, self._padsi_install_dir, vm_version.nickname)
        # self._viewer=nsbubble.vm.Viewer() # TO DEBUG, use the remote-viewer program

        self._vm_viewer_host_pid: int|None = None

        self._virtiofs_srvs: list[virtiofs.VirtioFSServer] = []
        self._virtiofs_data: list[nsbubble.VirtioSharedDirectory] = []
        self.syslog_prefix=f"VM_{self._uid}_{self._vm_conf.id}_{vm_version}/{vm_version.nickname}"
        self._firewall_denied_spec=firewall.LogSpec(self.syslog_prefix, global_conf.firewall_logs_group)

        self._web_infra_c: web_infra.WebInfra | None=None
        self._dns_c: dns.DNSServer|None=None
        self._dhcp_c: dhcp.DHCPServer|None=None
        self.net_mtu=mtu
        self._prepare_components()

    def _prepare_components(self):
        log_only = self.zone_conf.get_option(padsi.config.ZoneOptionType.NET_LOG_ONLY).enabled

        # Web infra (Web proxy or Web redirection option)
        if len(self.zone_conf.web_proxies)>0:
            proxy=_create_proxy(self.zone_conf, self._vm_conf, self._z_infra.bridge_ip if self._z_infra is not None else None)
            direct_rules=self.zone_conf.fw_rules
            if self.zone_conf.resolv_rules is not None:
                direct_rules=direct_rules+self.zone_conf.resolv_rules if direct_rules is not None else self.zone_conf.resolv_rules
            comp = web_infra.WebInfra(ipaddress.IPv4Interface(padsi.config.tap_ip),
                [proxy] if proxy is not None else None,
                False, # always disable web redirection (useless feature in a VM)
                direct_rules # pyright: ignore
            )

            if _debug and len(self.zone_conf.web_proxies)>0:
                syslog.syslog(syslog.LOG_DEBUG, f"{self.syslog_prefix}: created web infra with with proxy '{self.zone_conf.web_proxies}'")

            self.add_component(comp)
            self._web_infra_c=comp

        # DNS service (only if enabled in the zone the VM is running in)
        if self._z_infra is not None and self._vm_conf.network is not None:
            if self._z_infra.resolv_rules is None:
                resolv_rules=None
            else:
                resolv_rules=self._vm_conf.network.resolv_rules.copy() if self._vm_conf.network.resolv_rules is not None else []
                resolv_rules+=self._z_infra.resolv_rules
            resolver = padsi.config.network.DNSEndpoint.from_spec(str(self._z_infra.bridge_ip.ip)) # the resolver is the DNS server of the associated infra
            comp = dns.DNSServer(resolv_rules, [resolver], log_denied_spec=self._firewall_denied_spec, log_only=log_only)
            self.add_component(comp)
            self._dns_c=comp

            if self._web_infra_c is not None:
                rules=[]
                for name in ("wpad.", "proxy."):
                    rule=padsi.config.ResolvRule(action="allow", descr=f"Allow VM to {name}",
                        endpoint=firewall.Endpoint.from_repr(name), resolv=[f"A/3600/{str(padsi.config.tap_ip)}"])
                    rules.append(rule)
                comp.add_extra_rules("web-proxy", rules)

        # DHCP server
        comp = dhcp.DHCPServer(
            interfaces=["tapvm"],
            server_ip=ipaddress.IPv4Interface(f"{padsi.config.tap_ip}/24"),
            pool_start=padsi.config.vm_ip,
            pool_end=padsi.config.vm_ip,
            resolver_ips=[padsi.config.tap_ip],
            router_ips=[padsi.config.tap_ip],
            mtu=self.net_mtu
        )
        if _debug:
            syslog.syslog(syslog.LOG_DEBUG, f"{self.syslog_prefix}: ZoneVM's MTU: {self.net_mtu}")
        self.add_component(comp, False)
        self._dhcp_c=comp

        # static FW
        if self._z_infra is not None:
            fw_rules=[] if self._z_infra.fw_rules is None else self._z_infra.fw_rules
            fw_rules.append(padsi.config.FWRule(
                "allow",
                "Web proxy access",
                firewall.Endpoint.from_repr(f"{str(self._z_infra.bridge_ip.ip)} ^ tcp ^ 3128"),
                padsi.config.FWRuleChain.OUTPUT,
            ))
            if _debug:
                syslog.syslog(syslog.LOG_DEBUG, f"{self.syslog_prefix}: created static FW component for VM, {fw_rules=}")
            comp = stfw.StaticFirewall(
                fw_rules,
                log_denied_spec=self._firewall_denied_spec,
                log_only=log_only,
            )
            self.add_component(comp)

        # virtiofs component for the VM management's shared directory
        comp = virtiofs.VirtioFSServer(padsi.config.MountPoint("padsi-agent", self._vmm.management_files_dir, True))
        self.add_component(comp, False)
        self._virtiofs_srvs.append(comp)
        self._virtiofs_data.append(nsbubble.VirtioSharedDirectory(comp.fsname, comp.socket_path))

        # other virtiofs components (one per shared dir)
        if len(self._vm_conf.mount_points) > 0:
            user_xdg_subdirectories = compute_user_xdg_subdirectories(self.uid)
            for mp in self._vm_conf.mount_points:
                actual_mp = expand_variables_in_string(mp.mountpoint, user_xdg_subdirectories)
                actual_sp = expand_variables_in_string(mp.source_path, user_xdg_subdirectories)
                comp = virtiofs.VirtioFSServer(padsi.config.MountPoint(actual_mp, actual_sp, mp.readonly),
                    self._zuf.zone_home_dir)
                self.add_component(comp, False)
                self._virtiofs_srvs.append(comp)
                self._virtiofs_data.append(nsbubble.VirtioSharedDirectory(comp.fsname, comp.socket_path))

        # VM monitor
        comp = monitor.VMMonitor()
        self.add_component(comp, False)
        self._vm_monitor_component = comp

        # USB redirection
        comp = usbredir.USBRedir(os.path.join(self._run_dir, "padsi-usbredir.sock"), self.logs_dir)
        self.add_component(comp)

        # FW log
        logs_group=self.global_conf.firewall_logs_group
        if logs_group is not None:
            if _debug:
                syslog.syslog(syslog.LOG_DEBUG,f"{self.syslog_prefix}: created FW log (for group {logs_group})")
            comp=fw_logger.FWLogger(logs_group)
            self.add_component(comp)

    @property
    def ip_address(self) -> ipaddress.IPv4Interface|None:
        """IP address as an ipaddress.IPv4Interface of the zone from
        the point of view of the zone's network
        """
        return self._ip_address

    @property
    def home_dir(self) -> str:
        """Home directory for the zone (in the context of the "init" mount namespace)
        """
        return self._zuf.zone_home_dir

    def has_gui_processes(self) -> bool:
        """Tell if there are some processes in the zone which have a potential GUI
        """
        return True

    @property
    def firewall_log_spec(self) -> firewall.LogSpec:
        return self._firewall_denied_spec

    def compute_mount_points(self) -> dict:
        mounts=super().compute_mount_points()
        mounts.update(get_apps_generic_mount_points(self._z_infra.wayland_proxy_socket if self._z_infra is not None else None))

        mounts[self._vm_v.infos_file] = {
            "mount-point": self._vm_v.infos_file,
            "read-only": False,
            "monitored": False,
        }

        if not os.path.exists(self._viewer.bubble_prog_name):
            mounts[self._viewer.real_prog_name] = {
                "mount-point": self._viewer.bubble_prog_name,
                "read-only": True,
                "monitored": False,
            }

        # programs need access to have access to /sys to perform udev enumration and /run/udev for hotplug detection
        # beyond access to /dev/hidraw*
        mounts["/sys"] = {
            "mount-point": "/sys",
            "read-only": True,
            "monitored": False,
        }
        mounts["/run/udev"] = {
            "mount-point": "/run/udev",
            "read-only": True,
            "monitored": False,
        }

        # access to the user service, for USB devices management
        mounts[f"/run/user/{self._uid}/padsi-userv.sock"] = {
            "mount-point": "/bubble/run/padsi-userv.sock",
            "read-only": False,
            "monitored": False,
        }

        # access to the netlink host helper, required for USB redirection
        mounts[f"/run/user/{self._uid}/padsi-netlink.sock"] = {
            "mount-point": "/bubble/run/padsi-netlink.sock",
            "read-only": False,
            "monitored": False,
        }

        # set up LD_PRELOAD for the netlink shim, required for USB redirection
        script_dir = os.path.dirname(os.path.realpath(__file__))
        shim_lib = os.path.realpath(os.path.join(script_dir, "..", "..", "bin", "netlink-shim.so"))
        if not os.path.isfile(shim_lib):
            raise Exception(f"Netlink shim library '{shim_lib}' is missing")
        preload_file = os.path.join(self._run_dir, "netlink.preload")
        with open(preload_file, "wt") as fd:
            fd.write(f"{shim_lib}\n")
        mounts[preload_file] = {
            "mount-point": "/etc/ld.so.preload",
            "read-only": True,
            "monitored": False,
        }
        return mounts

    @property
    def features(self) -> nsbubble.Features:
        return nsbubble.Features(with_syslog=True)

    def create_bubble(self, features:nsbubble.Features) -> nsbubble.Bubble:
        return nsbubble.BubbleVM(self._vm_v.image_file, self._vm_v.vars_file, self._vm_conf.specs,
            features=features, boot_iso=self._boot_iso, extra_isos=self._extra_isos, vfs_dirs=self._virtiofs_data, run_dir=self.run_dir)

    def start(self):
        """Actually start the bubble (but not the VM)
        """
        try:
            super().start()

            # set VM state and allow to change the state from within the bubble, the parent directory also needs
            # to have write access to be able to write to the DB itself
            if self._vm_v.state is None:
                self._vm_v.set_state(VMState.CREATED, "Initial creation")

        except Exception as e:
            syslog.syslog(syslog.LOG_ERR, f"{self.syslog_prefix}: starting zone failed: {str(e)}")
            raise e

    def _grab_ssh_pubkeys(self) -> bool:
        """
        Returns: True if job done
        """
        if self.api is None:
            return False
        (host_tmp, bubble_dir) = self.api.create_shared_tempory_directory()
        pid = self.api.start_process(
            ["ssh-keyscan", "-q", "-T", "1", str(padsi.config.vm_ip)],
            ignore_status=False,
            child_stdout_file=os.path.join(bubble_dir, "out"),
            child_stderr_file=os.path.join(bubble_dir, "err"),
        )
        try:
            returncode = self.api.get_process_exit_status(pid, wait=1)
            if returncode != 0:
                return False
        except nsbubble.nsbubble.ProcessNotYetTerminatedException:
            return False

        vm_domain_names=[item[:-1] for item in self.domain_names] # remove the trailing "."

        with open(os.path.join(host_tmp.name, "out"), "rt") as fd:
            keys: list[str] = []
            for line in fd.readlines():
                line = line.strip()
                if not line or line[0] == "#":
                    continue  # ignore comments
                (_ip, key) = line.split(maxsplit=1)
                keys.append(key)

        if len(keys) > 0:
            known_hosts_file=None
            try:
                syslog.syslog(syslog.LOG_DEBUG, f"Got VM's server's SSH keys: {keys}")

                lock_file = os.path.join(self._vmm.zone_home_dir, ".ssh", ".padsi.lock")
                with open(lock_file, "w") as lockfd:
                    fcntl.flock(lockfd, fcntl.LOCK_EX)
                    try:
                        # integrate into the user's .ssh/known_hosts while retaining keys for other systems
                        known_hosts_file = os.path.join(self._vmm.zone_home_dir, ".ssh", "known_hosts")
                        with tempfile.NamedTemporaryFile("wt") as tmp:
                            # copy other data from known_hosts file if it exists
                            try:
                                with open(known_hosts_file, "rt") as fd:
                                    for line in fd.readlines():
                                        to_keep=True
                                        for dname in vm_domain_names:
                                            if line.startswith(f"{dname} "):
                                                to_keep=False
                                                break
                                        if to_keep:
                                            tmp.write(line)
                            except FileNotFoundError:
                                pass

                            # add our SSH keys, one line per VM domain name
                            for key in keys:
                                for dname in vm_domain_names:
                                    tmp.write(f"{dname} {key}\n")

                            # finalize
                            tmp.flush()
                            shutil.move(tmp.name, known_hosts_file)

                        # integrate into the user's .ssh/config while retaining other settings
                        config_file = os.path.join(self._vmm.zone_home_dir, ".ssh", "config")
                        with tempfile.NamedTemporaryFile("wt") as tmp:
                            # copy settings not related to the VM
                            spaced_names=" ".join(vm_domain_names)
                            try:
                                with open(config_file, "rt") as fd:
                                    do_copy = True
                                    for line in fd.readlines():
                                        if line.startswith("Host "):
                                            (_, targets) = line.split(maxsplit=1)
                                            targets = targets.strip()
                                            if targets == spaced_names:
                                                do_copy = False
                                            else:
                                                do_copy = True
                                                tmp.write(line)
                                        elif do_copy:
                                            tmp.write(line)
                            except FileNotFoundError:
                                pass

                            # add this VM's settings
                            tmp.write(f"Host {spaced_names}\n")
                            tmp.write("    IdentityFile ~/.ssh/padsi-vm-key\n")

                            # finalize
                            tmp.flush()
                            shutil.move(tmp.name, config_file)
                    finally:
                        # unlock the lock file
                        fcntl.flock(lockfd, fcntl.LOCK_UN)
                        lockfd.close()

                return True
            except Exception as e:
                syslog.syslog(
                    syslog.LOG_WARNING,
                    f"Failed to create SSH's known hosts file {known_hosts_file if known_hosts_file is not None else '_undefined_'}: {str(e)}",
                )
        else:
            syslog.syslog(
                syslog.LOG_WARNING, "VM's SSH server did not provide any public key???"
            )
        return False

    async def _propagate_ssh_server_pubkey(self):
        """Try to connect several times to the VM's SSH server to grab the public keys, and update the user's
        SSH config
        """
        counter = 0
        loop = asyncio.get_event_loop()
        while counter < 20:
            counter += 1
            await asyncio.sleep(2)
            if await loop.run_in_executor(None, self._grab_ssh_pubkeys):
                return
        syslog.syslog(syslog.LOG_INFO, "VM has no SSH server")

    def start_vm(self):
        """Actually start the VM"""
        if self.bubble is None or self.api is None:
            raise Exception("VM's zone has not yet been started")
        try:
            assert(isinstance(self.bubble, nsbubble.BubbleVM))
            vmbubble:nsbubble.BubbleVM=self.bubble
            ui_pid: int|None = None
            if not self._viewer.needs_spice_socket:
                # start the VM viewer (always present even if no UI is actually shown)
                ui_pid = vmbubble.vm_display(self._viewer)
                if _debug:
                    syslog.syslog(syslog.LOG_DEBUG, f"{self.syslog_prefix}: viewer PID in bubble is {ui_pid}")
                self._vm_viewer_host_pid = self.bubble.map_bubble_pid_to_host(ui_pid)

            # start the virtiofs components (before the VM)
            for component in self._virtiofs_srvs:
                component.start(self.api)

            # start the VM
            vm_pid = vmbubble.start_qemu()
            if _debug:
                syslog.syslog(syslog.LOG_DEBUG, f"{self.syslog_prefix}: started VM, QEMU PID: {vm_pid}")

            # start the DHCP server now
            assert(self._dhcp_c is not None)
            self._dhcp_c.start(self.api)

            # try to get the VM's SSH server public key to set up everything for the user
            self._server_ssh_key_task = asyncio.create_task(self._propagate_ssh_server_pubkey())

            if ui_pid is None:
                # start the VM viewer only now because it needs the VM's Spice socket to be present
                time.sleep(1)
                if _debug:
                    syslog.syslog(syslog.LOG_DEBUG, "Post starting VM viewer")
                ui_pid = vmbubble.vm_display(self._viewer)
                self._vm_viewer_host_pid = self.bubble.map_bubble_pid_to_host(ui_pid)
                if _debug:
                    syslog.syslog(syslog.LOG_DEBUG, f"{self.syslog_prefix}: viewer PID {ui_pid=}, {self._vm_viewer_host_pid=}")


            # VM monitor (start requires extra arguments)
            self._vm_monitor_component.start_monitor(
                self.api,
                image_file=self._vm_v.image_file,
                vars_file=self._vm_v.vars_file,
                infos_file=self._vm_v.infos_file,
                qemu_pid=vm_pid,
                viewer_pid=ui_pid,
            )
        except Exception as e:
            syslog.syslog(syslog.LOG_ERR, f"{self.syslog_prefix}: starting zone failed: {str(e)}")
            if self._vm_viewer_host_pid is not None:
                # kill the now useless VM viewer
                os.kill(self._vm_viewer_host_pid, signal.SIGTERM)
                self._vm_viewer_host_pid = None
            raise e

    @property
    def vm_conf(self) -> padsi.config.VirtualMachine:
        return self._vm_conf

    @property
    def vm_version(self) -> VMVersion:
        return self._vm_v

    @property
    def domain_names(self) -> list[str]:
        """Domain names through which the VM will be reachable using DNS
        Notes:
            - the names include a final ".vm."
            - the returned list contains at least one item
            - the user service may decide to remove som items in the list to avoid collisions
        """
        if self._domain_names is None:
            res: list[str] = []
            if self._vm_v.nickname is not None:
                cnickname=self._vm_v.nickname.replace('.', '-')
                res.append(f"{cnickname}.vm.") # short name, may result in collisions
                res.append(f"{cnickname}.{self._vm_conf.id}.vm.") # long name, no collision possible
            res.append(f"{self._vm_v.domain_name}.vm.") # short name, may result in collisions
            res.append(f"{self._vm_v.domain_name}.{self._vm_conf.id}.vm.") # long name, no collision possible
            self._domain_names=res
        return self._domain_names

    @domain_names.setter
    def domain_names(self, names:list[str]):
        """Change the domain names, necessary for the user service to avoid name collisions"""
        self._domain_names=names

    @property
    def vm_viewer_host_pid(self) -> int | None:
        """Get the PID of the VM viewer in the host PID namespace"""
        return self._vm_viewer_host_pid

    def add_dns_resolution_rules(self, context: str, rules: list[padsi.config.ResolvRule]):
        """Add some context specific DNS rules"""
        if self._dns_c is not None:
            self._dns_c.add_extra_rules(context, rules)


def zone_vm_setup(net_bubble_netns: str, net_bubble_init_pid: int, log_denied_spec: firewall.LogSpec):
    ns_nzone = None
    try:
        ns_nzone = nsbubble.named_netns_create(net_bubble_netns, net_bubble_init_pid)
        fw_zone_ns = firewall.Firewall(ns_nzone, log_denied_spec=log_denied_spec)

        # allow programs in the zone's bubble to communicate with the VM
        fw_zone_ns.flow_set_policy(
            firewall.FlowType.FILTER_OUTPUT,
            firewall.NetFlow.from_repr(f"*>>{str(padsi.config.vm_ip)}"),
            firewall.Policy.ALLOW,
        )

        # allow programs in all the bubbles of the same zone to connect to the VM using DNAT
        fw_zone_ns.add_dnat(padsi.config.vm_ip, in_iface=external_zone_iface)
    finally:
        if ns_nzone is not None:
            nsbubble.named_netns_remove(net_bubble_netns)
