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
# Network functions manipulations to "attach" apps zone or admin NS to some infrastructure
#

import ipaddress
import subprocess
import syslog

import firewall
import nsbubble
import padsi.network
from firewall.netflow import Endpoint
from padsi.config.trafficshaper import TrafficShaper

from .components import Component

external_zone_iface = "eth0"  # name of the interface in the bubble which allows communications with the world outside of the zone

def network_infra_setup(
    fw_init_ns: firewall.Firewall,
    traffic_shaper:TrafficShaper|None,
    veth_iface: str, # interface name in the "init" network NS
    net_bubble_netns: str,
    net_bubble_init_pid: int,
    net_bridge_ip: ipaddress.IPv4Interface,
    net_bridge_name: str,
    lower_net: ipaddress.IPv4Network,
    syslog_prefix: str,
    mtu: int|None
):
    """Set up host networking adaptations for a ZoneInfra or an AdminInfra
    """
    syslog.syslog(syslog.LOG_DEBUG, f"{syslog_prefix}: setup")
    ns_nzone = None
    lower_net_ns = traffic_shaper.net_ns if traffic_shaper is not None else None
    try:
        ns_nzone = nsbubble.named_netns_create(net_bubble_netns, net_bubble_init_pid)

        # network namespace and internal bridge
        padsi.network.bridge_add(net_bridge_name, net_bridge_ip, ns_nzone)
        padsi.network.interface_set_up("lo", True, ns_nzone)

        # veth to route to the host
        veth_zone = external_zone_iface
        padsi.network.veth_add(veth_iface, lower_net_ns, veth_zone, ns_nzone)

        addr_in_init_ns = ipaddress.IPv4Interface(f"{str(lower_net[1])}/{lower_net.prefixlen}")
        addr_in_infra_ns = ipaddress.IPv4Interface(f"{str(lower_net[2])}/{lower_net.prefixlen}")

        padsi.network.addr_add(veth_iface, addr_in_init_ns, lower_net_ns)
        padsi.network.interface_set_up(veth_iface, True, lower_net_ns, mtu)

        padsi.network.addr_add(veth_zone, addr_in_infra_ns, ns_nzone)
        padsi.network.interface_set_up(veth_zone, True, ns_nzone, mtu)
        padsi.network.route_add_default(veth_zone, addr_in_init_ns.ip, ns_nzone)

        # FW settings
        fw_zone_ns = firewall.Firewall(ns_nzone)
        fw_zone_ns.add_masquerade(out_iface=veth_zone)
        fw_zone_ns.set_default_policy(firewall.FlowType.FILTER_INPUT, firewall.Policy.ALLOW)

        fw_init_ns.add_masquerade(source_addr=addr_in_infra_ns.ip)
        fw_init_ns.set_default_policy(firewall.FlowType.FILTER_FORWARD, firewall.Policy.ALLOW)
        syslog.syslog(syslog.LOG_DEBUG, f"{syslog_prefix}: setup done")
    except Exception as e:
        syslog.syslog(syslog.LOG_ERR, f"{syslog_prefix}: setup failed: {str(e)}")
        try:
            padsi.network.interface_delete(veth_iface, lower_net_ns)
        except Exception as e:
            syslog.syslog(syslog.LOG_ERR,f"{syslog_prefix}: setup failed, while removing veth {veth_iface}, error: {str(e)}")
    finally:
        if ns_nzone is not None:
            nsbubble.named_netns_remove(net_bubble_netns)

def network_infra_cleanup(
    fw_init_ns: firewall.Firewall,
    traffic_shaper:TrafficShaper|None,
    veth_iface: str,
    run_dir: str,
    serialized_components: list[dict],
    lower_net: ipaddress.IPv4Network,
    syslog_prefix: str,
):
    """Remove host networking adaptations for a ZoneInfra or an AdminInfra
    """
    syslog.syslog(syslog.LOG_DEBUG, f"{syslog_prefix}: cleanup start")
    api = nsbubble.BubbleAPI(run_dir)
    for item in serialized_components:
        component=None
        try:
            component = Component.deserialize(item)
            component.stop(api)
        except Exception as e:
            syslog.syslog(syslog.LOG_ERR, f"{syslog_prefix}: cleanup failed to stop component {component.name if component is not None else item}: {str(e)}")

    # FW settings
    try:
        addr_in_infra_ns = ipaddress.IPv4Interface(f"{str(lower_net[2])}/{lower_net.prefixlen}")
        fw_init_ns.del_masquerade(source_addr=addr_in_infra_ns.ip)
    except Exception as e:
        syslog.syslog(syslog.LOG_ERR, f"{syslog_prefix}: cleanup failed to remove masquerade: {str(e)}")

    try:
        lower_net_ns = traffic_shaper.net_ns if traffic_shaper is not None else None
        padsi.network.interface_delete(veth_iface, lower_net_ns)
    except padsi.network.NetworkNamespaceNotFound:
        pass  # has already been removed (probably by a traffic shaper)
    except Exception as e:
        syslog.syslog(syslog.LOG_ERR, f"{syslog_prefix}: cleanup failed to remove veth {veth_iface}, error: {str(e)}")
    syslog.syslog(syslog.LOG_DEBUG, f"{syslog_prefix}: cleanup done")

def _allow_unprivileged_ping(netns:str, syslog_prefix:str):
    # properly set net.ipv4.ping_group_range
    proc = subprocess.run(["ip", "netns", "exec", netns, "/usr/sbin/sysctl", "net.ipv4.ping_group_range=0 2147483647",],
        capture_output=True, text=True)
    if proc.returncode != 0:
        syslog.syslog(syslog.LOG_ERR, f"{syslog_prefix}: could not allow unprivileged ping (could not set net.ipv4.ping_group_range): {proc.stderr}")

def network_infra_create_attach_netns(
    attached_netns: str,
    zone_addr: ipaddress.IPv4Interface,
    infra_netns: str,
    infra_init_pid: int,
    infra_bridge_ip: ipaddress.IPv4Interface,
    infra_bridge_name: str,
    syslog_prefix: str,
    mtu: int|None
):
    """Create and attach a namespace to the ZoneInfra using a pair of veth
    """
    try:
        if not padsi.network.netns_exists(attached_netns):
            padsi.network.netns_add(attached_netns)
            padsi.network.interface_set_up("lo", True, attached_netns)
    except Exception as e:
        syslog.syslog(syslog.LOG_ERR, f"{syslog_prefix}: could not create network namespace {attached_netns}: {str(e)}")
        raise e

    ns_infra = None
    try:
        ns_infra = nsbubble.named_netns_create(infra_netns, infra_init_pid)
        veth_infra = padsi.network.interface_create_name("ve", "admin")
        veth_izone = external_zone_iface
        padsi.network.veth_add(veth_izone, attached_netns, veth_infra, ns_infra)
        padsi.network.interface_set_up(veth_infra, True, ns_infra, mtu)

        padsi.network.addr_add(veth_izone, zone_addr, attached_netns)
        padsi.network.interface_set_up(veth_izone, True, attached_netns, mtu)
        padsi.network.route_add_default(veth_izone, infra_bridge_ip.ip, attached_netns)

        padsi.network.interface_attach_to_bridge(veth_infra, infra_bridge_name, ns_infra)

        fw = firewall.Firewall(attached_netns)
        fw.add_masquerade(out_iface=veth_izone)

        _allow_unprivileged_ping(attached_netns, syslog_prefix)

    except Exception as e:
        if ns_infra is not None:
            padsi.network.netns_delete(attached_netns)
        syslog.syslog(syslog.LOG_ERR, f"{syslog_prefix}: could not attach network namespace {attached_netns} to {ns_infra}: {str(e)}")
        raise e

    finally:
        try:
            if ns_infra is not None:
                nsbubble.named_netns_remove(infra_netns)
        except Exception:
            pass

def network_infra_dnat_incoming(
    endpoint:Endpoint,
    final_ip:ipaddress.IPv4Interface,
    infra_netns:str,
    infra_init_pid:int,
    lower_net:ipaddress.IPv4Network,
    lower_netns:str|None,
    syslog_prefix: str,):
    """Forward via DNAT some incoming traffic to a process running in an admin NS
    """
    ns_infra = None
    try:
        ns_infra = nsbubble.named_netns_create(infra_netns, infra_init_pid)
        # forward traffic in the admin infra
        fw=firewall.Firewall(infra_netns)
        fw.add_dnat(dest_addr=final_ip.ip, in_iface=None, protocol_spec=endpoint.protocols_as_string, port_spec=endpoint.ports_as_string)

        # forward traffic from the "init" or the VPN network NS
        fw_init_ns = firewall.Firewall(lower_netns, objects_prefix="padsi" if lower_netns is None else None)
        addr_in_infra_ns = ipaddress.IPv4Interface(f"{str(lower_net[2])}/{lower_net.prefixlen}")
        fw_init_ns.add_dnat(dest_addr=addr_in_infra_ns.ip, in_iface=None, protocol_spec=endpoint.protocols_as_string, port_spec=endpoint.ports_as_string)

    except Exception as e:
        syslog.syslog(syslog.LOG_ERR, f"{syslog_prefix}: could not DNAT incoming traffic to {endpoint}: {str(e)}")
        raise e

    finally:
        try:
            if ns_infra is not None:
                nsbubble.named_netns_remove(infra_netns)
        except Exception:
            pass

def network_infra_delete_netns(netns: str, syslog_prefix: str):
    try:
        if padsi.network.netns_exists(netns):
            padsi.network.netns_delete(netns)
    except Exception as e:
        syslog.syslog(syslog.LOG_ERR, f"{syslog_prefix}: could not delete network namespace {netns}: {str(e)}")
        raise e

def network_infra_attach_zone_apps(
    zone_netns: str,
    zone_init_pid: int,
    zone_addr: ipaddress.IPv4Interface,
    infra_netns: str,
    infra_init_pid: int,
    infra_bridge_ip: ipaddress.IPv4Interface,
    infra_bridge_name: str,
    syslog_prefix: str,
    mtu: int|None
):
    """Attach a zone apps to the ZoneInfra using a pair of veth
    """
    ns_zone = None
    ns_infra = None
    try:
        # attach zone network to zone's infra
        ns_zone = nsbubble.named_netns_create(zone_netns, zone_init_pid)
        ns_infra = nsbubble.named_netns_create(infra_netns, infra_init_pid)

        veth_infra = padsi.network.interface_create_name("ve", zone_netns)
        veth_izone = external_zone_iface
        padsi.network.veth_add(veth_izone, ns_zone, veth_infra, ns_infra)
        padsi.network.interface_set_up(veth_infra, True, ns_infra, mtu)

        padsi.network.addr_add(veth_izone, zone_addr, ns_zone)
        padsi.network.interface_set_up(veth_izone, True, ns_zone, mtu)
        padsi.network.route_add_default(veth_izone, infra_bridge_ip.ip, ns_zone)

        padsi.network.interface_attach_to_bridge(veth_infra, infra_bridge_name, ns_infra)

        fw = firewall.Firewall(ns_zone)
        fw.add_masquerade(out_iface=veth_izone)

        _allow_unprivileged_ping(ns_zone, syslog_prefix)

    except Exception as e:
        syslog.syslog(syslog.LOG_ERR, f"{syslog_prefix}: could not attach zone apps: {str(e)}")
        raise e

    finally:
        try:
            if ns_zone is not None:
                nsbubble.named_netns_remove(zone_netns)
        except Exception:
            pass
        try:
            if ns_infra is not None:
                nsbubble.named_netns_remove(infra_netns)
        except Exception:
            pass
