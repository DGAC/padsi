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
# PADSI object to hold infrastructure of a zone APPS
#
from __future__ import annotations

import asyncio
import ipaddress
import os
import syslog

import firewall
import nsbubble
import padsi.config
import padsi.network
from padsi.simple_comm import Message, MessageType, Server

from .components import dns, fw_logger
from .components import static_firewall as stfw
from .components import wayland_proxy, web_infra
from .zone_foundations import ZoneFoundations


class ZoneServer(Server):
    """Server which handles requests from the zone service"""
    def __init__(self, zone_conf: padsi.config.Zone):
        super().__init__()
        self.zone_conf = zone_conf

    async def handle_request(self, request: Message) -> Message:
        """Actually handle requests"""
        try:
            _cmde = request.data["cmde"]
        except Exception:
            raise Exception(f"Invalid request '{request.data}'")
        syslog.syslog(syslog.LOG_ERR, "TODO!!!")
        return Message(MessageType.REPLY, None)

class ZoneInfra(ZoneFoundations):
    """Object to set up and configure the network namespace and
    services associated to a zone definition
    Note: all the instances of a zone (ZoneIntance objects) are "associated" to
          the same ZoneInfra object
    """
    def __init__(
        self,
        global_conf: padsi.config.Configuration,
        zone_conf: padsi.config.Zone,
        uid: int,
        run_dir: str,
        logs_dir: str,
        lower_net: ipaddress.IPv4Network,
    ):
        super().__init__("INFRA", global_conf, zone_conf, None, uid, run_dir, logs_dir)

        self._br_name = "br0"
        self._br_last_addr_index = 0  # last assigned address
        self._br_ip: ipaddress.IPv4Interface = self.get_next_unused_ip()  # IP address of the bridge

        self._lower_veth = padsi.network.interface_create_name("lw", f"{zone_conf.name}-{uid}")
        self._lower_net: ipaddress.IPv4Network = lower_net

        self._web_infra_c: web_infra.WebInfra|None=None
        self._dns_c: dns.DNSServer|None=None
        self._wayland_proxy_c: wayland_proxy.WaylandProxy|None=None

        self._fw_rules: list[padsi.config.FWRule]|None = None
        self._resolv_rules: list[padsi.config.ResolvRule]|None = None

        self._server: ZoneServer|None = None
        self._server_task: asyncio.Task|None = None

        self._prepare_components()

    def _prepare_components(self):
        if self.zone_conf.network_enabled:
            web_redirection_option = self.zone_conf.get_option(padsi.config.ZoneOptionType.WEB_REDIRECTION)
            log_only = self.zone_conf.get_option(padsi.config.ZoneOptionType.NET_LOG_ONLY).enabled

            # Web infra (Web proxy or Web redirection option), must be first
            if len(self.zone_conf.web_proxies)>0 or web_redirection_option.enabled:
                direct_rules=self.zone_conf.fw_rules
                if self.zone_conf.resolv_rules is not None:
                    direct_rules=direct_rules+self.zone_conf.resolv_rules if direct_rules is not None else self.zone_conf.resolv_rules
                comp = web_infra.WebInfra(self.bridge_ip,
                    self.zone_conf.web_proxies,
                    web_redirection_option.enabled,
                    direct_rules # pyright: ignore
                )

                if len(self.zone_conf.web_proxies)>0:
                    syslog.syslog(syslog.LOG_DEBUG, f"{self.syslog_prefix}: created web infra with with proxy '{self.zone_conf.web_proxies}'")
                if web_redirection_option.enabled:
                    syslog.syslog(syslog.LOG_DEBUG, f"{self.syslog_prefix}: created web infra with static Web redirection {web_redirection_option=}")

                self.add_component(comp)

                if web_redirection_option.enabled and not self.zone_conf.has_dns_resolution:
                    syslog.syslog(syslog.LOG_WARNING, "Web redirection is enabled but DNS resolution is not, this will not work")
                self._web_infra_c=comp

            # DNS service
            opt=padsi.config.BlockListOption.downcast(self.zone_conf.get_option(padsi.config.ZoneOptionType.DNS_BLOCKLIST))
            fw_denied_spec=firewall.LogSpec(self.syslog_prefix, self.global_conf.firewall_logs_group)
            if self.zone_conf.has_dns_resolution:
                comp = dns.DNSServer(
                    self.resolv_rules,
                    self.zone_conf.dns_resolvers,
                    log_denied_spec=fw_denied_spec,
                    log_only=log_only,
                    denied_fallback_ip=str(self.bridge_ip.ip) if web_redirection_option.enabled else None,
                    has_web_proxy=len(self.zone_conf.web_proxies)>0,
                    dns_block_list=opt.blocklist_file if opt.enabled else None
                )
                syslog.syslog(syslog.LOG_DEBUG, f"{self.syslog_prefix}: created DNS server component, log_only: {log_only}")
                self.add_component(comp)
                self._dns_c=comp

                if self._web_infra_c is not None:
                    rules=[]
                    for name in ("wpad.", "proxy."):
                        rule=padsi.config.ResolvRule(action="allow", descr=f"Allow to {name}",
                            endpoint=firewall.Endpoint.from_repr(name), resolv=[f"A/3600/{str(self._br_ip.ip)}"])
                        rules.append(rule)
                    comp.add_extra_rules("web-proxy", rules)

            # static FW
            fw_rules=self.fw_rules
            if fw_rules is not None and len(fw_rules) > 0:
                syslog.syslog(syslog.LOG_DEBUG,f"{self.syslog_prefix}: created static FW component, log_only: {log_only}")
                comp = stfw.StaticFirewall(fw_rules, log_denied_spec=fw_denied_spec, log_only=log_only)
                self.add_component(comp)

            # FW log
            if self.logs_group is not None:
                syslog.syslog(syslog.LOG_DEBUG,f"{self.syslog_prefix}: created FW log (for group {self.logs_group})")
                comp=fw_logger.FWLogger(self.logs_group)
                self.add_component(comp)

        # Wayland Proxy
        if self.global_conf.zone_needs_wayland_proxy(self.zone_conf.name):
            allowed_zones=self.global_conf.get_clipboard_allowed_zones(self.zone_conf.name)
            comp=wayland_proxy.WaylandProxy(self.tmp_dir, self.zone_conf.name, allowed_zones)
            self.add_component(comp)
            self._wayland_proxy_c=comp

    @property
    def lower_iface(self) -> str:
        """Name of the veth network's veth peer located outside of the bubble."""
        return self._lower_veth

    @property
    def lower_net(self) -> ipaddress.IPv4Network:
        """Network which is used to link to the outside world (either directly in the "init" network namespace
        or in a network namespace where there is a WireGuard link).

        Ex.: 10.202.0.5/30
        """
        return self._lower_net

    @property
    def fw_rules(self) -> list[padsi.config.FWRule] | None:
        """Consolidated FW rules"""
        if self._fw_rules is None:
            self._fw_rules = []
            if self._web_infra_c is not None:
                (self._fw_rules, _) = self._web_infra_c.network_rules
            if self.zone_conf.fw_rules is not None:
                self._fw_rules += self.zone_conf.fw_rules
        return self._fw_rules

    @property
    def resolv_rules(self) -> list[padsi.config.ResolvRule] | None:
        """Consolidated resolv. rules"""
        if self._resolv_rules is None:
            self._resolv_rules = []
            if self._web_infra_c is not None:
                (_, self._resolv_rules) = self._web_infra_c.network_rules
            if self.zone_conf.resolv_rules is not None:
                self._resolv_rules+=self.zone_conf.resolv_rules
        return self._resolv_rules

    @property
    def http_proxy_env(self) -> dict[str, str] | None:
        """Get the HTTP proxy environment variables which should be used
        by programs running in the zone, points to the zone's web proxy component
        """
        if len(self.zone_conf.web_proxies)>0:
            value = f"http://{str(self._br_ip.ip)}:3128"
            return {"http_proxy": value, "https_proxy": value}
        return None

    @property
    def wayland_proxy_socket(self) -> str|None:
        return self._wayland_proxy_c.wayland_proxy_socket if self._wayland_proxy_c is not None else None

    @property
    def bridge_name(self) -> str:
        """Name of the bridge network interface in the zone
        """
        return self._br_name

    @property
    def bridge_ip(self) -> ipaddress.IPv4Interface:
        """IP address of the bridge in the zone
        """
        return self._br_ip

    def get_extra_trusted_ca(self) -> str | None:
        """Get the root certificate of the web redirection certification authority if any"""
        return None if self._web_infra_c is None else self._web_infra_c.get_root_cert()

    def compute_mount_points(self) -> dict:
        mounts=super().compute_mount_points()
        web_redirection_option = self.zone_conf.get_option(padsi.config.ZoneOptionType.WEB_REDIRECTION)
        if web_redirection_option.enabled:
            # add access to notifications service via its Unix socket
            mounts[f"/run/user/{self.uid}/padsi-notify.sock"] = {
                "mount-point": "/bubble/run/padsi-notify.sock",
                "read-only": False,
                "monitored": False,
            }
        return mounts

    @property
    def features(self) -> nsbubble.Features:
        return nsbubble.Features(
            bind_dev=True,  # necessary to be able to manipulate network FW (bwrap bug?)
            with_host_resolv=False,
            with_syslog=True
        )

    def start(self):
        """Start the bubble with the required components if necessary
        """
        super().start()

        # start zone server
        self._server = ZoneServer(self.zone_conf)
        path = os.path.join(os.path.realpath(os.path.dirname(__file__)), "zone-service")
        args = [
            path,
            self.zone_conf.config_dir,
            self.zone_conf.name,
            self.run_dir,
        ]
        self._server_task = asyncio.create_task(self._server.serve_client(args))
        #syslog.syslog(syslog.LOG_DEBUG, f"Zone service started {args=}, {os.environ=}")

    @property
    def zone_service_socket(self) -> str | None:
        """The socket file of the PADSI zone service where zone instances can use it"""
        return (
            None
            if self._server is None
            else os.path.join(self.run_dir, "padsi-zserv.sock")
        )

    def get_next_unused_ip(self) -> ipaddress.IPv4Interface:
        try:
            self._br_last_addr_index += 1
            a = padsi.config.users_br_network[self._br_last_addr_index]
            addr = ipaddress.IPv4Interface(f"{str(a)}/{padsi.config.users_br_network.prefixlen}")
            return addr
        except Exception:
            raise Exception(f"No more available IP in the network associated to zone '{self.zone_conf.name}'")

    def add_dns_resolution_rules(self, context: str, rules: list[padsi.config.ResolvRule]):
        """Add some context specific DNS rules"""
        if self._dns_c is not None:
            self._dns_c.add_extra_rules(context, rules)
