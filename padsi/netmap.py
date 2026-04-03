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
import ipaddress
import json
import os
import subprocess
from dataclasses import dataclass


def _netns_str(name:str|int|None, netns:str|int|None=None):
    if name is None:
        return f"in ns '{netns if netns else 'init'}'"
    return f"'{name}' in ns '{netns if netns else 'init'}'"

def _ip_with_netns(netns:str|int|None) -> list[str]:
    if netns is None:
        return []
    if isinstance(netns, str):
        return ["ip", "-n", netns]
    if isinstance(netns, int):
        if os.geteuid()!=0:
            raise Exception("Must be run as root")
        return ["nsenter", "-n", "-t", str(netns)]
    raise Exception(f"CODEBUG: invalid netns '{netns}'")

class NetworkInterfaceType(str, enum.Enum):
    """Network interface type
    """
    BRIDGE="Bridge"
    ETHERNET="Ethernet"
    LOOPBACK="Loopback"
    WIREGUARD="WireGuard"

    @staticmethod
    def from_str(iface_type:str) -> NetworkInterfaceType|None:
        match (iface_type):
            case "loopback":
                return NetworkInterfaceType.LOOPBACK
            case "ether":
                return NetworkInterfaceType.ETHERNET
            case _:
                return None

@dataclass
class NetworkInterface:
    """Represents a single network interface
    """
    name:str
    iface_type:NetworkInterfaceType|None
    index:int
    peer_index:int|None
    ns_or_pid:str|int|None=None # net NS name or pid of a process
    addresses:list[ipaddress.IPv4Interface|ipaddress.IPv6Interface]|None=None

def get_pids_with_a_network_namespaces() -> set[int]:
    """List all the process's PID which have unshared a net namespace
    """
    def _complement_from_data(item:dict, pids:set[int]):
        for subdata in item.get("children", []):
            _complement_from_data(subdata, pids)
        if item.get("type")!="net":
            return
        pids.add(item["pid"])

    args=["lsns", "-J"]
    proc=subprocess.run(args, capture_output=True, text=True)
    if proc.returncode!=0:
        raise Exception(f"Could not list network namespaces: {proc.stderr}")
    res:set[int]=set()
    for item in json.loads(proc.stdout)["namespaces"]:
        _complement_from_data(item, res)
    return res

def interface_get_addresses(ifname:str, netns_or_pid:str|int|None) -> list[ipaddress.IPv4Interface|ipaddress.IPv6Interface]:
    args=_ip_with_netns(netns_or_pid)+["ip", "-j", "address", "show", ifname]
    proc=subprocess.run(args, capture_output=True, text=True)
    if proc.returncode!=0:
        raise Exception(f"Could not list addresses of network interface {_netns_str(ifname, netns_or_pid)}: {proc.stderr}")
    res:list[ipaddress.IPv4Interface|ipaddress.IPv6Interface]=[]
    for addritem in json.loads(proc.stdout):
        try:
            for item in addritem["addr_info"]:
                match item.get("family"):
                    case "inet":
                        res.append(ipaddress.IPv4Interface(f"{item.get('local')}/{item.get('prefixlen')}"))
                    case "inet6":
                        res.append(ipaddress.IPv6Interface(f"{item.get('local')}/{item.get('prefixlen')}"))
        except Exception as e:
            raise Exception(f"CODEBUG: could not analyse output of ip address {_netns_str(ifname, netns_or_pid)}: {str(e)}")
    return res

def list_network_interfaces(netns_or_pid:str|int|None) -> dict[str,NetworkInterface]:
    """List all the interfaces in a specified network namespace:
        - the init NS if the netns argument is None
        - a PID if the netns argument is an int
        - a net NS (as listed by 'ip netns') name if the netns argument is a string
    """
    # list WireGuard interfaces
    args=_ip_with_netns(netns_or_pid)+["wg", "show"]
    wg_list:list[str]=[]
    try:
        proc=subprocess.run(args, capture_output=True, text=True)
        if proc.returncode!=0:
            raise Exception(f"Could not list WireGuard network interfaces: {proc.stderr}")
        for line in proc.stdout.splitlines():
            if line.startswith("interface: "):
                wg_list.append(line[:10].strip())
    except FileNotFoundError:
        pass

    # list Bridge interfaces
    args=_ip_with_netns(netns_or_pid)+["ip", "-j", "link", "show", "type", "bridge"]
    br_list:list[str]=[]
    try:
        proc=subprocess.run(args, capture_output=True, text=True)
        if proc.returncode!=0:
            raise Exception(f"Could not list bridge network interfaces: {proc.stderr}")
        for item in json.loads(proc.stdout):
            br_list.append(item.get("ifname"))
    except FileNotFoundError:
        pass

    # list network interfaces
    args=_ip_with_netns(netns_or_pid)+["ip", "-j", "link"]
    proc=subprocess.run(args, capture_output=True, text=True)
    if proc.returncode!=0:
        raise Exception(f"Could not list network interfaces {_netns_str(None, netns_or_pid)}: {proc.stderr}")
    res:dict[str, NetworkInterface]={}
    for ifitem in json.loads(proc.stdout):
        iface=NetworkInterface(ns_or_pid=netns_or_pid, name=ifitem.get("ifname"),
                               index=ifitem.get("ifindex"),
                               peer_index=ifitem.get("link_index"),
                               iface_type=NetworkInterfaceType.from_str(ifitem.get("link_type")))
        if iface.iface_type is None and iface.name in wg_list:
            iface.iface_type=NetworkInterfaceType.WIREGUARD
        elif iface.name in br_list:
            iface.iface_type=NetworkInterfaceType.BRIDGE

        iface.addresses=interface_get_addresses(iface.name, netns_or_pid)
        res[iface.name]=iface
    return res

class NetworkMap:
    def __init__(self):
        self._by_ns:dict[int,dict[str,NetworkInterface]]={}
        self._ns_pids:set[int]=set()
        self._analyse()

    def _analyse(self):
        self._by_ns={}
        self._ns_pids=get_pids_with_a_network_namespaces()
        for pid in self._ns_pids:
            self._by_ns[pid]=list_network_interfaces(pid)

    def get_ns_interfaces(self, net_ns:int|None=None) -> list[NetworkInterface]:
        if net_ns is None:
            net_ns=1 # init's PID
        ifaces=self._by_ns.get(net_ns)
        if ifaces is None:
            return []
        return list(ifaces.values())

    def get_interface(self, if_name:str, net_ns:int|None=None) -> NetworkInterface|None:
        if net_ns is None:
            net_ns=1 # init's PID
        ifaces=self._by_ns.get(net_ns)
        if ifaces is None:
            return None
        return ifaces.get(if_name)

    def get_peer_interface(self, iface:NetworkInterface) -> NetworkInterface|None:
        """Get the peer network interface of a VETH network interface
        """
        for (_, ifaces) in self._by_ns.items():
            for (_, oiface) in ifaces.items():
                if oiface.index==iface.peer_index:
                    return oiface
        return None
