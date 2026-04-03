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
import json
import os
import socket
import syslog

import firewall
import padsi.network as network


class TrafficShaper:
    """Represent a special routing for one or more zones, to be inherited by each actual implementation e.g. like:
    - routing through a VPN
    - split tunneling
    - ...

    This base object sets up a dedicated network namespace in which the actual traffic shaper implements its magic (e.g. by
    running a process which creates a TUN/TAP interface and defines the associated routing). However, the VETH to actually
    allow communications outside of that network namespace must be done by the traffic shaper's implementation
    """

    def __init__(self, name: str):
        self._netns_name: str = self.__class__.__name__
        self._ts_type: str = self.__class__.__name__
        self._name = name
        self._need_setup: bool=True
        self._init_veth_iface:str|None=None

    @property
    def name(self) -> str:
        return self._name

    @property
    def ts_type(self) -> str:
        """Class name of the traffic shaper"""
        return self._ts_type

    @property
    def net_ns(self) -> str:
        """Network namespace (usable with "ip -n") of the object"""
        return self._netns_name

    @net_ns.setter
    def net_ns(self, netns_name:str):
        self._netns_name=netns_name

    @property
    def need_setup(self) -> bool:
        return self._need_setup

    @property
    def net_ns_exists(self) -> bool:
        """Tell if the network namespace exists (does not mean the Wireguard interface is present though)"""
        return network.netns_exists(self._netns_name)

    @property
    def functionnal(self) -> bool | None:
        """Tell if the traffic shaper is fully functionnal, or None if it can't be tested"""
        # to be overridden by actual implementation
        return None

    @property
    def need_dns_resolution(self) -> bool:
        """Tell if DNS resolution will be needed in order to setup the traffic shaper"""
        return False

    @property
    def need_veth(self) -> bool:
        """Tell if the traffic shaper needs a veth between the 'init' network namespace
        and its dedicated network namespace
        """
        return True

    @property
    def veth_iface_name(self) -> str|None:
        """Name of the veth interface in the 'init' network NS, if need_veth if not None
        """
        return self._init_veth_iface

    @property
    def net_mtu(self) -> int | None:
        """Get the MTU imposed by the traffic shaper"""
        return None

    def setup(self, fw_init_ns:firewall.Firewall, lower_net:ipaddress.IPv4Network|None):
        """Set up the resources: create (or re-create) the network namespace"""
        self._need_setup=False
        if self.net_ns_exists:
            network.netns_delete(self._netns_name)

        syslog.syslog(syslog.LOG_INFO, f"Setting up network ns '{self._netns_name}'")
        try:
            network.netns_add(self._netns_name)
            network.interface_set_up("lo", True, self._netns_name)

            fw_zone_ns = firewall.Firewall(self._netns_name)
            fw_zone_ns.set_default_policy(firewall.FlowType.FILTER_FORWARD, firewall.Policy.ALLOW)

            if lower_net is not None:
                # set up the VETH link between the "init" net NS and self.net_ns if not yet done
                veth_iface=network.interface_create_name(self.net_ns, "") # interface name in the "init" network NS
                self._init_veth_iface=veth_iface
                veth_tsp="eth-init" # don't use eth0, it's already used by the network infra
                if not network.interface_exists(veth_iface):
                    syslog.syslog(syslog.LOG_INFO, f"Creating veth for traffic shaper '{self.name}'")
                    network.veth_add(veth_iface, None, veth_tsp, self.net_ns)

                    addr_in_init_ns = ipaddress.IPv4Interface(f"{str(lower_net[1])}/{lower_net.prefixlen}")
                    addr_in_tsp_ns = ipaddress.IPv4Interface(f"{str(lower_net[2])}/{lower_net.prefixlen}")

                    network.addr_add(veth_iface, addr_in_init_ns)
                    network.interface_set_up(veth_iface, True)

                    network.addr_add(veth_tsp, addr_in_tsp_ns, self.net_ns)
                    network.interface_set_up(veth_tsp, True, self.net_ns)
                    network.route_add_default(veth_tsp, addr_in_init_ns.ip, self.net_ns)

                    # FW settings
                    fw_zone_ns.add_masquerade(out_iface=veth_tsp)
                    fw_init_ns.add_masquerade(source_addr=addr_in_tsp_ns.ip)
                    fw_init_ns.set_default_policy(firewall.FlowType.FILTER_FORWARD, firewall.Policy.ALLOW)

            syslog.syslog(syslog.LOG_DEBUG, f"Setting up network ns '{self._netns_name}' done")
        except Exception as e:
            syslog.syslog(syslog.LOG_ERR, f"Failed to set up network ns '{self._netns_name}': {str(e)}")
            raise e

    def destroy(self, fw_init_ns:firewall.Firewall):
        """Destroy any resources which have been set up"""
        if self.net_ns_exists:
            network.netns_delete(self._netns_name)

    async def resolv(self, name: str) -> ipaddress.IPv4Address | None:
        """Try to resolve a name, returns the IPv4 address or None if it could not"""
        to=socket.getdefaulttimeout()
        try:
            socket.setdefaulttimeout(5)
            counter=0
            while True:
                try:
                    res=ipaddress.IPv4Address(socket.gethostbyname(name))
                    return res
                except socket.gaierror as e:
                    if e.errno==-3:
                        counter+=1
                        if counter<30: # wait a bit for the "connection to the world" DNS resolution to work
                            await asyncio.sleep(0.5)
                        else:
                            syslog.syslog(syslog.LOG_ERR, f"DNS resolution failed: {str(e)}")
                            return None
                    else:
                        syslog.syslog(syslog.LOG_ERR, f"DNS resolution failed: {str(e)}")
                        return None
                except Exception as e:
                    syslog.syslog(syslog.LOG_ERR, f"DNS resolution failed: {str(e)}")
                    return None
        finally:
            socket.setdefaulttimeout(to)

    async def adapt(self, dns_resolvers_found: bool, host_fw: firewall.Firewall):
        """Function called whenever the /etc/resolv.conf file changes or the traffic shaper is not functional
        """
        pass

def load_from_file(path: str) -> TrafficShaper:
    """Load the contents of a .tsp file"""
    with open(path, "r") as fd:
        data = json.load(fd)
        if not isinstance(data, dict):
            raise Exception(f"Invalid traffic shaper resources file '{path}'")

        parts = os.path.basename(path).split(".")
        if len(parts) != 2:
            raise Exception(f"Invalid traffic shaper file name '{path}'")
        name = parts[0]
        match data.get("type"):
            case "wireguard":
                from .shappers import wireguard
                return wireguard.WireGuardTrafficShaper.from_data(name, data, os.path.dirname(path))
            case "openvpn":
                from .shappers import openvpn
                return openvpn.OpenVPNTrafficShaper.from_data(name, data, os.path.dirname(path))
            case _:
                raise Exception(f"Unknown traffic shaper type '{data.get('type')}'")
