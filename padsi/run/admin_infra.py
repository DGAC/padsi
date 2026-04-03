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
# PADSI object to hold the infrastructure of an admin NS
#
from __future__ import annotations

import ipaddress
import os
import syslog

import firewall
import nsbubble
import padsi.config
import padsi.network
from padsi.config.trafficshaper import TrafficShaper

from .components import dns, fw_logger
from .components import static_firewall as stfw
from .components import web_infra
from .zone_foundations import ZoneFoundations


class AdminInfra(ZoneFoundations):
    """Object to set up and configure the network namespace and
    services associated to a zone definition
    Note: all the instances of a zone (ZoneIntance objects) are "associated" to
          the same ZoneInfra object
    """
    ns_prefix="admns-"

    @classmethod
    def get_admin_ns_name(cls, admin_conf: padsi.config.AdminNS) -> str:
        if len(admin_conf.web_proxies)==0:
            return f"{AdminInfra.ns_prefix}{admin_conf.name}-p" # lower case p
        else:
            return f"{AdminInfra.ns_prefix}{admin_conf.name}-P" # upper case P

    def __init__(
        self,
        global_conf: padsi.config.Configuration,
        admin_conf: padsi.config.AdminNS,
        uid: int,
        run_dir: str,
        logs_dir: str,
        lower_net: ipaddress.IPv4Network,
        tsp: TrafficShaper|None
    ):
        super().__init__("ADMININFRA", global_conf, None, admin_conf, uid,
            os.path.join(run_dir, f"{AdminInfra.ns_prefix}{admin_conf.name}"),
            os.path.join(logs_dir, f"{AdminInfra.ns_prefix}{admin_conf.name}"))

        self._tsp=tsp
        if tsp is not None:
            self.net_mtu=tsp.net_mtu

        self._br_name = "br0"
        a = padsi.config.admin_br_network[1]
        self._br_ip = ipaddress.IPv4Interface(f"{str(a)}/{padsi.config.admin_br_network.prefixlen}") # IP address of the bridge
        a= padsi.config.admin_br_network[2]
        self._admin_ip = ipaddress.IPv4Interface(f"{str(a)}/{padsi.config.admin_br_network.prefixlen}") # IP address of the veth in the admin NS

        self._ns_name=AdminInfra.get_admin_ns_name(admin_conf)

        self._lower_veth = padsi.network.interface_create_name("lw", f"{admin_conf.name}-{uid}")
        self._lower_net: ipaddress.IPv4Network = lower_net

        self._web_infra_c: web_infra.WebInfra|None=None

        self._firewall_denied_spec=firewall.LogSpec(self.syslog_prefix, global_conf.firewall_logs_group)

        self._fw_rules: list[padsi.config.FWRule]|None = None
        self._resolv_rules: list[padsi.config.ResolvRule]|None = None

        self._prepare_components()

    def _prepare_components(self):
        # Web infra (Web proxy or Web redirection option)
        if len(self.admin_conf.web_proxies)>0:
            direct_rules=self.admin_conf.out_fw_rules
            if self.admin_conf.out_resolv_rules is not None:
                direct_rules=direct_rules+self.admin_conf.out_resolv_rules if direct_rules is not None else self.admin_conf.out_resolv_rules
            comp = web_infra.WebInfra(self._br_ip,
                self.admin_conf.web_proxies,
                False,
                direct_rules # pyright: ignore
            )
            if len(self.admin_conf.web_proxies)>0:
                syslog.syslog(syslog.LOG_DEBUG, f"{self.syslog_prefix}: created web infra with with proxy '{self.admin_conf.web_proxies}'")
            self.add_component(comp)
            self._web_infra_c=comp

        # DNS service
        if self.admin_conf.has_dns_resolution:
            # Add DNS server component
            comp = dns.DNSServer(
                self.out_resolv_rules,
                self.admin_conf.dns_resolvers,
                log_denied_spec=self._firewall_denied_spec,
                log_only=False,
                denied_fallback_ip=None,
                has_web_proxy=len(self.admin_conf.web_proxies)>0
            )
            syslog.syslog(syslog.LOG_DEBUG, f"{self.syslog_prefix}: created DNS server component")
            self.add_component(comp)
            self._dns_component = comp

        # static FW
        fw_rules=self.out_fw_rules
        if fw_rules is not None and len(fw_rules) > 0:
            syslog.syslog(syslog.LOG_DEBUG,f"{self.syslog_prefix}: created static FW component")
            comp = stfw.StaticFirewall(
                fw_rules,
                log_denied_spec=self._firewall_denied_spec,
                log_only=False,
            )
            self.add_component(comp)

        # FW log
        logs_group=self.global_conf.firewall_logs_group
        if logs_group is not None:
            syslog.syslog(syslog.LOG_DEBUG,f"{self.syslog_prefix}: created FW log (for group {logs_group})")
            comp=fw_logger.FWLogger(logs_group)
            self.add_component(comp)

    @property
    def traffic_shaper(self) -> TrafficShaper|None:
        return self._tsp

    @property
    def lower_iface(self) -> str:
        """Name of the veth network's veth peer located outside of the bubble."""
        return self._lower_veth

    @property
    def lower_net(self) -> ipaddress.IPv4Network:
        """Network which is used to link to the outside world (either directly in the "init" network namespace
        or in a network namespace where there is a WireGuard link).

        Ex.: 10.202.255.253/30
        """
        return self._lower_net

    @property
    def out_fw_rules(self) -> list[padsi.config.FWRule] | None:
        """Consolidated output FW rules"""
        if self._fw_rules is None:
            self._fw_rules = []
            if self._web_infra_c is not None:
                (self._fw_rules, _) = self._web_infra_c.network_rules
            if self.admin_conf.out_fw_rules is not None:
                self._fw_rules += self.admin_conf.out_fw_rules
        return self._fw_rules

    @property
    def out_resolv_rules(self) -> list[padsi.config.ResolvRule] | None:
        """Consolidated resolv. rules"""
        if self._resolv_rules is None:
            self._resolv_rules = []
            if self._web_infra_c is not None:
                (_, self._resolv_rules) = self._web_infra_c.network_rules
            if self.admin_conf.out_resolv_rules is not None:
                self._resolv_rules+=self.admin_conf.out_resolv_rules
        return self._resolv_rules

    @property
    def http_proxy_env(self) -> dict[str, str] | None:
        """HTTP proxy environment variables which should be used
        by programs running in the zone, points to the zone's web proxy component
        """
        if len(self.admin_conf.web_proxies)>0:
            value = f"http://{str(self._br_ip.ip)}:3128"
            return {"http_proxy": value, "https_proxy": value}
        return None

    @property
    def bridge_name(self) -> str:
        """Name of the bridge network interface in the zone
        """
        return self._br_name

    @property
    def bridge_ip(self) -> ipaddress.IPv4Interface:
        """IP address of the bridge in the admin infra
        (invariant, normally 192.168.128.1)
        """
        return self._br_ip

    @property
    def admin_ip(self) -> ipaddress.IPv4Interface:
        """IP address of the veth interface in the admin NS
        (invariant, normally 192.168.128.2)
        """
        return self._admin_ip

    @property
    def admin_ns_name(self) -> str:
        """Persistent admin NS name in which padsi-do
        can run processes (i.e. the "XXX" in /run/netns/XXX)"""
        return self._ns_name

    @property
    def features(self) -> nsbubble.Features:
        return nsbubble.Features(
            bind_dev=True,  # necessary to be able to manipulate network FW (bwrap bug?)
            with_host_resolv=False,
            with_syslog=True
        )
