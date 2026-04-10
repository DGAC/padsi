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

import ipaddress
import json
import os
import re
import subprocess
import syslog

import padsi.misc


class NetworkNamespaceNotFound(Exception):
    pass

def _with_netns(name: str | None, netns: str | None = None):
    if name is not None:
        return f"'{name}' in ns '{netns if netns else 'init'}'"
    return f"in ns '{netns if netns else 'init'}'"

def _ip_command(netns: str | None):
    if netns:
        return ["ip", "-n", netns]
    return ["ip"]


#
# network namespaces
#
def netns_exists(name: str) -> bool:
    """Tells if a network namespace exists"""
    return os.path.exists(f"/run/netns/{name}")

def netns_check_exists(name: str | None):
    """Ensure that a network namespace exists.
    Does nothing if name is None or "", or raises NetworkNamespaceNotFound() if not found
    """
    if name and not netns_exists(name):
        raise NetworkNamespaceNotFound(f"Network namespace '{name}' does not exist")

def netns_delete(name: str):
    """Ensure that a network namespace does not exist"""
    if netns_exists(name):
        # NB: it seems network interfaces present in a network namespace are also
        #     deleted when the namespace is deleted (in case of veth, both ends are deleted)
        proc=subprocess.run(["ip", "netns", "del", name], capture_output=True, text=True)
        if proc.returncode!=0:
            raise Exception(f"Could not delete network namespace '{name}': {proc.stderr} (status: {proc.returncode})")

def netns_add(name: str):
    """Add a new network namespace"""
    proc=subprocess.run(["ip", "netns", "add", name], capture_output=True, text=True)
    if proc.returncode!=0:
        raise Exception(f"Could not add network namespaces '{name}': {proc.stderr} (status: {proc.returncode})")

def netns_list() -> list[str]:
    proc = subprocess.run(["ip", "netns", "ls"], capture_output=True, text=True)
    if proc.returncode!=0:
        raise Exception(f"Failed to list named network namespaces: {proc.stderr} (status: {proc.returncode})")
    res = []
    for line in proc.stdout.splitlines():
        (ns, *_) = line.split()
        res.append(ns)
    return res


#
# network interfaces
#
def interface_create_name(prefix: str, suffix: str):
    """Create a network interface name which respects the 15 chars maximum imposed by the Linux
    kernel"""
    global _interface_create_name_counter
    _interface_create_name_counter += 1
    r = 15 - len(prefix)
    suf = f"{_interface_create_name_counter}{suffix}"
    if len(suf) > r:
        suf = suf[len(suf) - r :]
    return f"{prefix}{suf}"


# to avoid any duplicate interface name which might lead to errors if several tasks use that function at the same time
# does not of course protect against several processes trying to get an interface name
_interface_create_name_counter: int = 0


def list_interfaces(netns: str | None = None) -> dict[str, ipaddress.IPv4Interface]:
    """List all network interfaces and the associated IPv4 address in the specified namespace"""
    res = {}
    args = _ip_command(netns) + ["-j", "address"]
    proc = subprocess.run(args, capture_output=True, text=True)
    if proc.returncode!=0:
        raise Exception(f"Could not list network interfaces {_with_netns(None, netns)}: {proc.stderr} (status: {proc.returncode})")
    for entry in json.loads(proc.stdout):
        ifname = entry.get("ifname")
        if ifname is None:
            syslog.syslog(syslog.LOG_WARNING, f"Unhandled ip -j contents {entry}")
        elif ifname != "lo":
            for addr in entry.get("addr_info", []):
                if addr.get("family") == "inet":
                    try:
                        res[ifname] = ipaddress.IPv4Interface(f"{addr.get('local')}/{addr.get('prefixlen')}")
                    except Exception:
                        syslog.syslog(syslog.LOG_WARNING, f"Unhandled addr_info's contents {addr}")
    return res

def interface_exists(name: str, netns: str | None = None) -> bool:
    """Tells if a network interface exists"""
    netns_check_exists(netns)
    args = _ip_command(netns) + ["link", "show", name]
    (status, _out, err) = padsi.misc.exec_sync(args)
    if status != 0:
        if "does not exist" in err:
            return False
        syslog.syslog(syslog.LOG_WARNING, f"Could not get information about network interface {_with_netns(name, netns)}: {err} ({' '.join(args)})",)
        return False
    return True

def interface_index(name: str, netns: str | None = None) -> int|None:
    """Get the index of a network interface, or None if the interface does not exist
    """
    netns_check_exists(netns)
    args = _ip_command(netns) + ["link", "show", name]
    proc=subprocess.run(args, capture_output=True, text=True)
    if proc.returncode != 0:
        if "does not exist" in proc.stderr:
            return None
        syslog.syslog(syslog.LOG_WARNING, f"Could not get information about network interface {_with_netns(name, netns)}: {proc.stderr} ({' '.join(args)})",)
        return None
    (id, *_)=proc.stdout.split(":")
    try:
        return int(id)
    except Exception:
        syslog.syslog(syslog.LOG_ERR, f"ip link returned unexpected data '{proc.stdout}'")
        return None

def interface_check_exists(name: str, netns: str | None = None):
    if not interface_exists(name, netns):
        raise Exception(f"Network interface {_with_netns(name, netns)} does not exist")

def interface_delete(name: str, netns: str | None = None):
    """Delete a network interface
    Does nothing if the interface does not exist
    """
    if interface_exists(name, netns):
        args = _ip_command(netns) + ["link", "del", name]
        (status, _out, err) = padsi.misc.exec_sync(args)
        if status != 0:
            raise Exception(f"Could not delete network interface {_with_netns(name, netns)}: {err}")

def interface_is_up(name: str, netns: str | None = None) -> bool | None:
    """Tell if an interface is UP
    Returns None if the interface does not exist
    """
    if interface_exists(name, netns):
        args = _ip_command(netns) + ["-j", "link", "show", name]
        proc = subprocess.run(args, capture_output=True, text=True)
        if proc.returncode != 0:
            raise Exception(f"Could not get state of interface {_with_netns(name, netns)}: {proc.stderr} (status: {proc.returncode})")
        data = json.loads(proc.stdout)
        try:
            return "UP" in data[0]["flags"]
        except Exception as e:
            raise Exception(f"CODEBUG: Could not get state of interface {_with_netns(name, netns)}: {str(e)}")
    return None

def interface_set_up(name: str, up: bool, netns: str | None = None, mtu:int|None=None):
    """Change the UP/DOWN state of an interface"""
    if interface_exists(name, netns):
        args = _ip_command(netns) + ["link", "set", "dev", name, "up" if up else "down"]
        (status, _out, err) = padsi.misc.exec_sync(args)
        if status != 0:
            raise Exception(f"Could not set state of interface {_with_netns(name, netns)} to {'up' if up else 'down'}: {err}")
        if mtu is not None:
            args = _ip_command(netns) + ["link", "set", "dev", name, "mtu", str(mtu)]
            proc=subprocess.run(args, capture_output=True, text=True)
            if proc.returncode!=0:
                raise Exception(f"Could not set MTU of interface {_with_netns(name, netns)} to {mtu}: {err}")

def interface_attach_to_bridge(name: str, bridge: str, netns: str | None = None):
    """Attach a network interface to a bridge
    NB: both the interface and the bridge must exist in the same network namespace
    """
    interface_check_exists(name, netns)
    interface_check_exists(bridge, netns)
    args = _ip_command(netns) + ["link", "set", name, "master", bridge]
    (status, _out, err) = padsi.misc.exec_sync(args)
    if status != 0:
        raise Exception(f"Could not attach interface {_with_netns(name, netns)} to bridge {_with_netns(bridge, netns)}: {err}")

def interface_move_to_namespace(name: str, new_netns: str, current_netns: str | None = None):
    """Move an existing network interface to a new namespace"""
    interface_check_exists(name, current_netns)
    if interface_exists(name, new_netns):
        raise Exception(f"Network interface {_with_netns(name, new_netns)} already exists (interface_move_to_namespace)")

    if current_netns:
        # bring interface to the 'init' namespace
        if interface_exists(name):
            raise Exception("Network interface '{name}' already exists in the 'init' namespace, we can't use the 'init' namespace as a 'staging' namespace")
        args = _ip_command(current_netns) + ["netns", "exec", current_netns, "ip", "link", "set",
            "netns", "1", "dev", name]
        (status, _out, err) = padsi.misc.exec_sync(args)
        if status != 0:
            raise Exception(f"Could not move network interface {_with_netns(name, current_netns)} to the 'init' namespace: {err} (status: {status})")

    if new_netns:
        # move to the new namespace
        args = _ip_command(current_netns) + ["link", "set", "netns", new_netns, "dev", name]
        (status, _out, err) = padsi.misc.exec_sync(args)
        if status != 0:
            try:
                interface_move_to_namespace(name, new_netns=str(current_netns))
            except Exception:
                pass
            raise Exception(f"Could not move network interface {_with_netns(name, 'init')} to the '{new_netns}' namespace: {err} (status: {status})")

def get_default_interfaces(netns: str|None=None) -> set[str]:
    """Get the names of the interfaces for the default route, ordered by metric"""
    args = _ip_command(netns) + ["-j", "route", "show", "default"]
    proc = subprocess.run(args, capture_output=True, text=True)
    if proc.returncode == 0:
        try:
            ifaces={}
            for item in json.loads(proc.stdout):
                ifaces[item.get("metric", 100)]=item["dev"]
            ifaces={k: v for k, v in sorted(ifaces.items(), key=lambda item: item[1])}
            return set(ifaces.values())
        except Exception as e:
            raise Exception(f"Could not get default route's interface of {_with_netns(netns, 'init')}: {str(e)}")
    raise Exception(f"Could not get default route's interface of {_with_netns(netns, 'init')}: {proc.stderr} (status: {proc.returncode})")


#
# network interfaces' addresses
#
def addr_exists(addr: ipaddress.IPv4Interface | ipaddress.IPv4Address, netns: str | None = None):
    """Get the name of the interface having the specified address,
    Returns None if no interface has that address
    NB: the netmask is ignored here
    """
    netns_check_exists(netns)
    args = _ip_command(netns) + ["addr", "list"]
    (status, out, _err) = padsi.misc.exec_sync(args)
    if status != 0:
        raise Exception(f"Could not list network addresses in ns {netns if netns else 'init'}")
    iface = None
    for line in out.splitlines():
        if re.match(r"^[0-9]+:", line):
            (_, iface, *_) = line.split(":")
            iface = iface.strip()
        elif re.match(r"^[ ]*inet ", line):
            (_, cidr, *_) = line.split()
            net = ipaddress.IPv4Interface(cidr)
            if isinstance(addr, ipaddress.IPv4Interface) and net == addr and net.ip == addr.ip:
                return iface
            elif isinstance(addr, ipaddress.IPv4Address) and net.ip == addr:
                return iface
    return None


def addr_add(iface: str, addr: ipaddress.IPv4Interface, netns: str | None = None):
    """Add an IP address to a network interface, does nothing if address is already present"""
    eiface = addr_exists(addr, netns)
    if eiface is not None:
        if eiface == iface:
            return
        else:
            raise Exception(f"Can't add address '{str(addr)}' to {_with_netns(iface, netns)}: it is already used by {_with_netns(eiface, netns)}")

    args = _ip_command(netns) + ["addr", "add", str(addr), "dev", iface]
    (status, _out, err) = padsi.misc.exec_sync(args)
    if status != 0:
        raise Exception(f"Could not add address '{str(addr)}' to {_with_netns(iface, netns)}: {err}")


def addr_get(iface: str, netns: str | None = None) -> ipaddress.IPv4Interface | None:
    """Get the current IP address of an interface
    Raise an exception if there is more than one IPv4 address associated to the interface.
    """
    args = _ip_command(netns) + ["addr", "show", iface]
    (status, out, err) = padsi.misc.exec_sync(args)
    if status != 0:
        raise Exception(f"Could not get the network addresses in ns {netns if netns else 'init'} of interface '{iface}': {err}")

    addr = None
    for line in out.splitlines():
        if re.match(r"^[ ]*inet ", line):
            if addr:
                raise Exception(f"Interface '{iface}' has more than one IPv4 address in ns {netns if netns else 'init'}")
            (_, addr, *_) = line.split()
    return ipaddress.IPv4Interface(addr) if addr else None


#
# veth
#
def veth_add(name: str, netns: str|None, peer_name: str, peer_netns: str):
    """Create a veth peer each in its own namespace
    Pass None as netns or peer_netns to use the "init" namespace
    """
    netns_check_exists(netns)
    netns_check_exists(peer_netns)

    # ip link add veth-i type veth peer name veth-t
    if name == peer_name:
        raise Exception("Can't create veth where both endpoints have the same name")
    if interface_exists(name, netns):
        raise Exception(f"Network interface {_with_netns(name, netns)} already exists (veth_add)")
    if interface_exists(peer_name, peer_netns):
        raise Exception(f"Peer network interface {_with_netns(peer_name, peer_netns)} already exists (veth_add)")
    args = _ip_command(netns) + [
        "link",
        "add",
        name,
        "type",
        "veth",
        "peer",
        "name",
        peer_name,
    ]
    (status, _out, err) = padsi.misc.exec_sync(args)
    if status != 0:
        raise Exception(f"Could not create veth interfaces {_with_netns(name, netns)} <-> {_with_netns(peer_name, peer_netns)}: {err}")

    # change peer veth to its target ns
    if netns != peer_netns:
        if netns:
            args = ["ip", "-n", netns, "link", "set", peer_name, "netns", peer_netns]
        else:
            args = ["ip", "link", "set", peer_name, "netns", peer_netns]
        (status, _out, err) = padsi.misc.exec_sync(args)
        if status != 0:
            raise Exception(f"Could not move veth interface {_with_netns(peer_name, netns)} to {_with_netns(peer_name, peer_netns)}: {err}")


#
# Bridge
#
def bridge_add(name: str, addr: ipaddress.IPv4Interface, netns: str | None = None):
    """Add a new bridge, in the "init" namespace or in the specified namespace"""
    if interface_exists(name, netns):
        raise Exception(f"Network interface {_with_netns(name, netns)} already exists (bridge_add)")
    # ip -n "$nsname" link set dev "$brname" up
    #    ip -n "$nsname" addr add 192.168.13.1/24 dev "$brname"
    try:
        # create bridge
        args = _ip_command(netns) + ["link", "add", name, "type", "bridge"]
        (status, _out, err) = padsi.misc.exec_sync(args)
        if status != 0:
            raise Exception(f"Could not add network bridge {_with_netns(name, netns)}: {err}")

        # configure addr
        args = _ip_command(netns) + ["addr", "add", str(addr), "dev", name]
        (status, _out, err) = padsi.misc.exec_sync(args)
        if status != 0:
            raise Exception(f"Could not set cidr of bridge {_with_netns(name, netns)}: {err}")

        # bring it up
        interface_set_up(name, True, netns)

    except Exception:
        interface_delete(name, netns)


#
# TAP
#
def tap_add(name: str, addr: ipaddress.IPv4Interface, netns: str | None = None, user: str | None = None):
    """Add a new TAP interface in the "init" namespace or in the specified namespace"""
    if interface_exists(name, netns):
        raise Exception(f"Network interface {_with_netns(name, netns)} already exists (tap_add)")
    try:
        # create bridge
        args = _ip_command(netns) + ["tuntap", "add", "dev", name, "mode", "tap"]
        if user is not None:
            args += ["user", user]
        (status, _out, err) = padsi.misc.exec_sync(args)
        if status != 0:
            raise Exception(f"Could not add tap interface {_with_netns(name, netns)}: {err}")

        # configure addr
        args = _ip_command(netns) + ["addr", "add", str(addr), "dev", name]
        (status, _out, err) = padsi.misc.exec_sync(args)
        if status != 0:
            raise Exception(f"Could not set cidr of tap {_with_netns(name, netns)}: {err}")

        # bring it up
        interface_set_up(name, True, netns)

    except Exception:
        interface_delete(name, netns)


#
# routing
#
def route_add_default(iface: str, gw_addr: ipaddress.IPv4Address | None, netns: str | None = None):
    """Add a default route through the specified interface
    Note:
    - if gw_addr is not None, then routing is done via that "next hop" and the network interface is
      only a hint for the kernel
    - otherwise, all the traffic is pushed via the specified network interface
    """
    interface_check_exists(iface, netns)
    if gw_addr is None:
        args = _ip_command(netns) + ["route", "add", "default", "dev", iface]
    else:
        args = _ip_command(netns) + [
            "route",
            "add",
            "default",
            "via",
            str(gw_addr),
            "dev",
            iface,
        ]

    (status, _out, err) = padsi.misc.exec_sync(args)
    if status != 0:
        raise Exception(f"Could not add default via {_with_netns(iface, netns)}: {err}")
