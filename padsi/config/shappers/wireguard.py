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
import os
import subprocess
import syslog
import tempfile

import firewall
import padsi.network

from ..trafficshaper import TrafficShaper


class WireGuardTrafficShaper(TrafficShaper):
    """Traffic shaper which routes all the traffic through a WireGuard network interface
    """
    def __init__(
        self,
        name: str,
        config_file: str,
        ping_test_ip: str | None,
        wg_iface: str | None,
    ):
        super().__init__(name)
        self._ts_type = "WireGuard"

        self._ping_test: ipaddress.IPv4Address | None = None
        if ping_test_ip is not None:
            try:
                self._ping_test = ipaddress.IPv4Address(ping_test_ip)
            except ipaddress.AddressValueError:
                raise Exception(f"Invalid ping test IP address '{ping_test_ip}'")

        if wg_iface is not None:
            self._wg_iface_name = wg_iface
            self.net_ns = f"wgns-{self._wg_iface_name}"
        else:
            parts = os.path.basename(config_file).split(".")
            self._wg_iface_name = "wg"
            self.net_ns = f"wgns-{parts[0]}"

        self._wg_server_name: str | None = None
        self._wg_port: int = 51820
        self._wg_server_ip: ipaddress.IPv4Address | None = None
        self._config_address: ipaddress.IPv4Interface
        self._config_file = config_file
        self._allowed_flow: firewall.NetFlow | None = None

    def __str__(self) -> str:
        return f"WireGuard({self._config_file})"

    @property
    def functionnal(self) -> bool | None:
        res=None
        if self._ping_test is not None:
            try:
                args = ["ip", "netns", "exec", self.net_ns, "ping", "-c", "1", str(self._ping_test)]
                proc = subprocess.run(args, timeout=1, capture_output=True)
                if proc.returncode!=255:
                    res=proc.returncode == 0
            except subprocess.TimeoutExpired:
                res=False
        syslog.syslog(syslog.LOG_INFO, f"Wireguard tshaper {self.name} is {res}")
        return res

    @property
    def need_dns_resolution(self) -> bool:
        try:
            ipaddress.IPv4Address(self._wg_server_name)
            return False
        except Exception:
            return True

    @property
    def need_veth(self) -> bool:
        return False

    @property
    def net_mtu(self) -> int | None:
        return 1420

    def _analyse_config_file(self):
        """Analyse the config file if not yet done"""
        if self._wg_server_name is not None:
            return

        if not os.path.exists(self._config_file):
            raise Exception(f"WireGuard configuration file '{self._config_file}' does not exist")

        try:
            with open(self._config_file, "rt") as fd:
                for line in fd.readlines():
                    if line.startswith("Address"):
                        (_, address_s) = line.split("=")
                        address_s = address_s.strip()
                        self._config_address = ipaddress.IPv4Interface(address_s)
                    elif line.startswith("Endpoint"):
                        (_, ep_s) = line.split("=")
                        ep_s = ep_s.strip()
                        (self._wg_server_name, *wg_port) = ep_s.split(":")
                        if len(wg_port) > 0:
                            self._wg_port = int(wg_port[0])
            if self._config_address is None:
                raise Exception("no address specified")
            if not self._wg_server_name:
                raise Exception("no server endpoint specified")
            if self._wg_port <= 0 or self._wg_port > 65535:
                raise Exception(f"invalid port {self._wg_port}")
        except Exception as e:
            raise Exception(f"Invalid WireGuard config file '{self._config_file}': {str(e)}")

    async def adapt(self, dns_resolvers_found: bool, host_fw: firewall.Firewall):
        self._analyse_config_file()
        wg_server_ip: ipaddress.IPv4Address|None=None
        try:
            # may work if WG server is specified as an IP address
            wg_server_ip = ipaddress.IPv4Address(self._wg_server_name)
        except Exception:
            if dns_resolvers_found:
                syslog.syslog(syslog.LOG_DEBUG, f"Resolving {str(self._wg_server_name)}...")
                wg_server_ip = await self.resolv(str(self._wg_server_name))
                syslog.syslog(syslog.LOG_DEBUG, f"Resolved {str(self._wg_server_name)} to {wg_server_ip}")
            else:
                syslog.syslog(syslog.LOG_DEBUG, "No DNS resolver available")

        syslog.syslog(syslog.LOG_DEBUG, f"Adapting Wireguard traffic shaper for interface '{self._wg_iface_name}' (server {self._wg_server_name})")

        if wg_server_ip == self._wg_server_ip:
            # nothing changed, nothing to do
            return

        # remove any previously allowed flow
        if self._allowed_flow is not None:
            try:
                syslog.syslog(syslog.LOG_DEBUG, f"Removing previous Wireguard flow '{self._allowed_flow}' for interface '{self._wg_iface_name}'")
                host_fw.flow_delete_policy(firewall.FlowType.FILTER_OUTPUT, self._allowed_flow)
            except Exception as e:
                msg=f"Could not remove WireGuard network flow '{self._allowed_flow}': {str(e)}"
                syslog.syslog(syslog.LOG_ERR, msg)
                raise Exception(msg)
            finally:
                self._allowed_flow = None

        if wg_server_ip is None:
            # don't known where to connect => discard WireGuard interface
            syslog.syslog(syslog.LOG_DEBUG, f"Could not resolve Wireguard server name '{self._wg_server_name}', discarding interface '{self._wg_iface_name}'")
            self._clean_namespace()
            self._wg_server_ip = None
        else:
            syslog.syslog(syslog.LOG_DEBUG, f"Got address {wg_server_ip} for WireGuard server, creating interface {self._wg_iface_name}")
            self._clean_namespace()
            try:
                self._create_interface(wg_server_ip)
                self._wg_server_ip = wg_server_ip
            except Exception as e:
                self._wg_server_ip = None
                msg=f"Could not create WireGuard interface in traffic shaper's namespace: {str(e)}"
                syslog.syslog(syslog.LOG_ERR, msg)
                raise Exception(msg)

            # allowing communications with the WireGuard server itself; the communications are initiated in the "init" namespace,
            # hence the FILTER_OUTPUT chain.
            self._allowed_flow = firewall.NetFlow(None, firewall.Endpoint.from_repr(f"{wg_server_ip} ^ udp ^ {self._wg_port}"))
            try:
                syslog.syslog(syslog.LOG_DEBUG, f"Allowing network flow to the WireGuard server '{self._allowed_flow=}'")
                host_fw.flow_set_policy(
                    firewall.FlowType.FILTER_OUTPUT,
                    self._allowed_flow,
                    firewall.Policy.ALLOW,
                )
            except Exception as e:
                self._allowed_flow = None
                msg=f"Could not allow network flow to the WireGuard server '{self._allowed_flow}': {str(e)}"
                syslog.syslog(syslog.LOG_ERR, msg)
                raise Exception(msg)

    def _clean_namespace(self):
        for net_ns in (None, self.net_ns):
            try:
                padsi.network.interface_delete(self._wg_iface_name, netns=net_ns)
            except Exception as e:
                syslog.syslog(syslog.LOG_WARNING, f"Could not remove WireGuard interface {self._wg_iface_name} from net NS {net_ns}: {str(e)}")

    def _create_interface(self, wg_server_addr: ipaddress.IPv4Address):
        # prepare config file accepted by "wg setconf" and extract the IP address of the interface
        try:
            tmp = tempfile.NamedTemporaryFile("wt")
            with open(self._config_file, "rt") as fd:
                for line in fd.readlines():
                    if line.startswith("Endpoint"):
                        tmp.write(f"Endpoint = {wg_server_addr}:{self._wg_port}\n")
                    elif line.startswith("AllowedIPs"):
                        tmp.write("AllowedIPs = 0.0.0.0/0\n")
                    elif line.startswith("Address"):
                        pass  # ignore that key
                    elif line.startswith("DNS"):
                        pass  # ignore that key
                    else:
                        tmp.write(line)
                tmp.flush()
        except Exception as e:
            raise Exception(f"Invalid WireGuard config file '{self._config_file}': {str(e)}")

        # set up the WG interface
        if padsi.network.interface_exists(self._wg_iface_name):
            padsi.network.interface_delete(self._wg_iface_name)

        try:
            proc = subprocess.run(
                ["ip", "link", "add", "dev", self._wg_iface_name, "type", "wireguard"],
                capture_output=True,
                text=True,
                timeout=1,
            )
            if proc.returncode != 0:
                raise Exception(f"Could not create WireGuard interface '{self._wg_iface_name}': {proc.stderr}")

            proc = subprocess.run(
                ["wg", "setconf", self._wg_iface_name, tmp.name],
                capture_output=True,
                text=True,
                timeout=1,
            )
            if proc.returncode != 0:
                raise Exception(f"Could not configure WireGuard interface '{self._wg_iface_name}' with config derived from '{self._config_file}': {proc.stderr}")

            # attach WG interface to the namespace
            padsi.network.interface_move_to_namespace(self._wg_iface_name, new_netns=self.net_ns)
            padsi.network.interface_set_up(self._wg_iface_name, True, self.net_ns)
            padsi.network.addr_add(self._wg_iface_name, self._config_address, self.net_ns)

            padsi.network.route_add_default(self._wg_iface_name, None, self.net_ns)

            fw = firewall.Firewall(self.net_ns)
            fw.add_masquerade(out_iface=self._wg_iface_name)
        except Exception as e:
            self._clean_namespace()
            raise e

    @classmethod
    def from_data(cls, name: str, data: dict, config_dir: str) -> WireGuardTrafficShaper:
        conf = data.get("config")
        if conf is None or conf.get("file") is None:
            raise Exception("Invalid traffic shaper configuration")
        conf_file = conf.get("file")
        if not os.path.isabs(conf_file):
            conf_file = os.path.join(config_dir, conf_file)
        return WireGuardTrafficShaper(name, conf_file, conf.get("ping-test"), conf.get("interface"))
