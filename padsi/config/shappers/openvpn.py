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

import asyncio
import ipaddress
import os
import signal
import subprocess
import syslog

import firewall
from padsi.network import interface_index

from ..trafficshaper import TrafficShaper


class OpenVPNTrafficShaper(TrafficShaper):
    """Traffic shaper which routes all the traffic through an OpenVPN connection
    """
    def __init__(self, name: str, config_file: str, ping_test_ip: str | None):
        super().__init__(name)
        self.net_ns = f"ovpnns-{self.name}"
        self._ts_type = "OpenVPN"
        self._config_file = config_file

        self._ping_test: ipaddress.IPv4Address | None = None
        if ping_test_ip is not None:
            try:
                self._ping_test = ipaddress.IPv4Address(ping_test_ip)
            except ipaddress.AddressValueError:
                raise Exception(f"Invalid ping test IP address '{ping_test_ip}'")

        self._vpn_iface_name="tun0"
        self._vpn_iface_idndex:int|None=None
        self._vpn_server_name:str|None=None
        self._vpn_port:int|None=None

        self._proc:subprocess.Popen|None=None
        self._monit_task:asyncio.Task|None=None

        self._blocked_flow: firewall.NetFlow|None=None # block all traffic which goes out of the network namespace ("killswitch")
        self._allowed_flow: firewall.NetFlow|None=None # allow traffic to the VPN server


    def __str__(self) -> str:
        return f"OpenVPN({self._config_file})"

    @property
    def net_iface(self) -> str:
        """Name of the OpenVPN network interface"""
        return self._vpn_iface_name

    @property
    def functionnal(self) -> bool | None:
        res=None
        if self._ping_test is not None:
            try:
                args=["ip", "netns", "exec", self.net_ns, "ping", "-c", "1", str(self._ping_test)]
                proc = subprocess.run(args, timeout=1, capture_output=True)
                if proc.returncode!=255:
                    res=proc.returncode == 0
            except subprocess.TimeoutExpired:
                res=False
        syslog.syslog(syslog.LOG_INFO, f"OpenVPN tshaper {self.name} is {res}")
        return res

    @property
    def need_dns_resolution(self) -> bool:
        try:
            ipaddress.IPv4Address(self._vpn_server_name)
            return False
        except Exception:
            return True

    @property
    def need_veth(self) -> bool:
        return True

    @property
    def net_mtu(self) -> int | None:
        return None

    def _analyse_config_file(self):
        """Analyse the config file if not yet done"""
        if self._vpn_server_name is not None:
            return

        if not os.path.exists(self._config_file):
            raise Exception(f"OpenVPN configuration file '{self._config_file}' does not exist")

        try:
            proto:str|None=None
            with open(self._config_file, "rt") as fd:
                for line in fd.readlines():
                    if line.startswith("remote "):
                        (_, srv, port, proto)=line.split()
                        self._vpn_server_name=srv
                        self._vpn_port=int(port)
            if not self._vpn_server_name:
                raise Exception("no server endpoint specified")
            if self._vpn_port is not None:
                if self._vpn_port <= 0 or self._vpn_port > 65535:
                    raise Exception(f"invalid port {self._vpn_port}")
            else:
                match proto:
                    case "udp":
                        self._vpn_port=1194
                    case "tcp":
                        self._vpn_port=443
                    case _:
                        raise Exception(f"unknown protocol'{proto}'")

        except Exception as e:
            raise Exception(f"Invalid OpenVPN config file '{self._config_file}': {str(e)}")

    def setup(self, fw_init_ns:firewall.Firewall, lower_net:ipaddress.IPv4Network|None):
        super().setup(fw_init_ns, lower_net)
        nflow=firewall.NetFlow(firewall.Endpoint.from_repr(f"#{self.veth_iface_name}"), None)
        self._blocked_flow=nflow
        try:
            fw_init_ns.flow_set_policy(firewall.FlowType.FILTER_FORWARD, nflow, firewall.Policy.DENY)
        except Exception as e:
            syslog.syslog(syslog.LOG_ERR, f"Could not deny killswitch network flow '{nflow}': {str(e)}")

    async def _monit(self):
        """Monitors the presence of the VPN process and associated TUN interface and adjust masquerading"""
        while True:
            try:
                await asyncio.sleep(2)
                # VPN process
                if self._proc is not None:
                    if self._proc.returncode is not None:
                        # process has terminated
                        self._proc.wait()
                        self._proc=None

                # TUN interface
                id=interface_index(self._vpn_iface_name, self.net_ns)
                if id is not None:
                    if self._vpn_iface_idndex!=id:
                        if self._vpn_iface_idndex is not None:
                            try:
                                fw=firewall.Firewall(self.net_ns)
                                fw.del_stale_masquerade(id)
                            except Exception:
                                pass
                            self.undeclare_default_route_interface(self._vpn_iface_name)

                        self._vpn_iface_idndex=id
                        try:
                            fw=firewall.Firewall(self.net_ns)
                            fw.add_masquerade(self._vpn_iface_name)
                        except Exception as e:
                            syslog.syslog(syslog.LOG_ERR, f"Failed to add masquerade to OpenVPN interface '{self._vpn_iface_name}': {str(e)}")
                        self.declare_default_route_interface(self._vpn_iface_name)
                else:
                    if self._vpn_iface_idndex is not None:
                        id=self._vpn_iface_idndex
                        self._vpn_iface_idndex=None
                        try:
                            fw=firewall.Firewall(self.net_ns)
                            fw.del_stale_masquerade(id)
                        except Exception:
                            pass
                        self.undeclare_default_route_interface(self._vpn_iface_name)
            except asyncio.CancelledError:
                return
            except Exception as e:
                syslog.syslog(syslog.LOG_ERR, f"Monitoring task failed: {str(e)}")
                return

    async def adapt(self, dns_resolvers_found: bool, host_fw: firewall.Firewall):
        self._analyse_config_file()

        # we already have a running instance, test if the VPN still works
        if self._proc is not None:
            if self.functionnal:
                return

            # kill existing VPN service
            try:
                self._proc.send_signal(signal.SIGTERM)
                self._proc.wait()
            except Exception:
                pass
            self._proc=None

            # remove any previously allowed flow
            self.undeclare_default_route_interface(self._vpn_iface_name)
            if self._allowed_flow is not None:
                try:
                    syslog.syslog(syslog.LOG_DEBUG, f"Removing previous allowed VPN flow '{self._allowed_flow}'")
                    host_fw.flow_delete_policy(firewall.FlowType.FILTER_FORWARD, self._allowed_flow)
                except Exception as e:
                    msg=f"Could not remove VPN network flow '{self._allowed_flow}': {str(e)}"
                    syslog.syslog(syslog.LOG_ERR, msg)
                    raise Exception(msg)
                finally:
                    self._allowed_flow=None

        # get the IP of the VPN server
        vpn_server_ip: ipaddress.IPv4Address|None=None
        try:
            # may work if VPN server is specified as an IP address
            vpn_server_ip = ipaddress.IPv4Address(self._vpn_server_name)
        except Exception:
            if dns_resolvers_found:
                syslog.syslog(syslog.LOG_DEBUG, f"Resolving {str(self._vpn_server_name)}...")
                vpn_server_ip = await self.resolv(str(self._vpn_server_name))
                syslog.syslog(syslog.LOG_DEBUG, f"Resolved {str(self._vpn_server_name)} to {vpn_server_ip}")
            else:
                syslog.syslog(syslog.LOG_DEBUG, "No DNS resolver available")

        syslog.syslog(syslog.LOG_DEBUG, f"Adapting OpenVPN traffic shaper for interface '{self._vpn_iface_name}' (server {self._vpn_server_name})")

        # allowing communications with the VPN server itself; the communications are initiated in the namespace where the VPN server
        # is running, hence the FORWARD chain
        self._allowed_flow=firewall.NetFlow(None, firewall.Endpoint.from_repr(f"{vpn_server_ip} ^ udp ^ {self._vpn_port}"))
        try:
            host_fw.flow_set_policy(firewall.FlowType.FILTER_FORWARD, self._allowed_flow, firewall.Policy.ALLOW)
        except Exception as e:
            self._allowed_flow = None
            msg=f"Could not allow network flow to the VPN server '{self._allowed_flow}': {str(e)}"
            syslog.syslog(syslog.LOG_ERR, msg)
            raise Exception(msg)

        assert(self._blocked_flow)
        try:
            host_fw.flow_delete_policy(firewall.FlowType.FILTER_FORWARD, self._blocked_flow)
        except Exception as e:
            syslog.syslog(syslog.LOG_ERR, f"Could not remove deny killswitch network flow '{self._blocked_flow}': {str(e)}")
        try:
            host_fw.flow_set_policy(firewall.FlowType.FILTER_FORWARD, self._blocked_flow, firewall.Policy.DENY)
        except Exception as e:
            syslog.syslog(syslog.LOG_ERR, f"Could not deny killswitch network flow '{self._blocked_flow}': {str(e)}")

        # launch the OpenVNP server
        try:
            self._proc=subprocess.Popen(["ip", "netns", "exec", self.net_ns, "openvpn", "--config", self._config_file])
            self._monit_task=asyncio.create_task(self._monit())
        except Exception as e:
            syslog.syslog(syslog.LOG_ERR, f"Could not start the OpenVPN server: {str(e)}")
            raise e

    def destroy(self, fw_init_ns:firewall.Firewall):
        if self._monit_task is not None:
            self._monit_task.cancel()
        if self._blocked_flow is not None:
            try:
                fw_init_ns.flow_delete_policy(firewall.FlowType.FILTER_FORWARD, self._blocked_flow)
            except Exception as e:
                syslog.syslog(syslog.LOG_WARNING, f"Could not remove deny killswitch network flow '{self._blocked_flow}': {str(e)}")
        if self._allowed_flow is not None:
            try:
                fw_init_ns.flow_delete_policy(firewall.FlowType.FILTER_FORWARD, self._allowed_flow)
            except Exception as e:
                syslog.syslog(syslog.LOG_WARNING, f"Could not remove VPN network flow '{self._allowed_flow}': {str(e)}")
        super().destroy(fw_init_ns)

    @classmethod
    def from_data(cls, name: str, data: dict, config_dir: str) -> OpenVPNTrafficShaper:
        conf = data.get("config")
        if conf is None or conf.get("file") is None:
            raise Exception("Invalid traffic shaper configuration")
        conf_file = conf.get("file")
        if not os.path.isabs(conf_file):
            conf_file = os.path.join(config_dir, conf_file)
        return OpenVPNTrafficShaper(name, conf_file, conf.get("ping-test"))
