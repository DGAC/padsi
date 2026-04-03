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

import json
import os

from . import network
from .proxy import Proxy
from .trafficshaper import TrafficShaper

_debug = False

class AdminNS:
    """Contains the configuration of an Admin namespace (the equivalent of a zone but for adminitration purposes)
    """
    def __init__(
        self,
        name: str,
        friendly_name: str | None,
        net: network.NetworkSpec,
        in_fw_rules: list[network.FWRule],
        proxies: list[Proxy] | None,
        config_dir: str,
        traffic_shaper: TrafficShaper|None
    ):
        self._name = name
        self._friendly_name = friendly_name
        self._network = net
        self._in_fw_rules=in_fw_rules
        self._web_proxies = proxies if proxies is not None else []
        self._config_dir = config_dir
        self._traffic_shaper=traffic_shaper

    @classmethod
    def from_data(
        cls,
        name: str,
        data: dict,
        named_netres: dict[str, network.NetworkRessources] | None,
        traffic_shapers: dict[str, TrafficShaper],
        config_dir: str,
    ) -> AdminNS:
        # zone's attributes
        friendly_name = data.get("friendly-name")
        if friendly_name is not None and not isinstance(friendly_name, str):
            raise Exception(f"Invalid friendly-name attribute '{friendly_name}")

        # traffic shapers
        tsp:TrafficShaper|None=None
        tsp_name=data.get("traffic-shaper")
        if tsp_name is not None:
            tsp=traffic_shapers.get(tsp_name)
            if tsp is None:
                raise Exception(f"Unknown traffic shaper '{tsp_name}'")

        # web proxies
        proxy_data=data.get("web-proxy")
        proxies:list[Proxy]=[]
        if proxy_data is not None:
            if not isinstance(proxy_data, list):
                raise Exception("Invalid 'web-proxy' section")
            for proxy_conf in proxy_data:
                proxy = Proxy.from_data(proxy_conf, named_netres)
                if proxy is not None:
                    proxies.append(proxy)

        # network
        netdata:dict|None=data.get("network")
        net = network.NetworkSpec.from_data(netdata, named_netres, None, config_dir=config_dir)
        if netdata is None or net is None:
            raise Exception(f"Admin NS '{name}' must have a network configuration")

        (in_fw_rules, in_resolv_rules) = network.load_rules_from_data(netdata.get("in-rules", []), named_netres)
        if len(in_resolv_rules)>0:
            raise Exception("Resolv. rules are not allowed as input rules")

        return cls(name, friendly_name, net, in_fw_rules, proxies, config_dir, tsp)

    @property
    def name(self) -> str:
        return self._name

    @property
    def friendly_name(self) -> str:
        return self._friendly_name if self._friendly_name else self._name

    @property
    def config_dir(self) -> str:
        """Directory containing the zone's config file"""
        return self._config_dir

    @property
    def network_spec(self) -> network.NetworkSpec:
        return self._network

    @property
    def traffic_shaper(self) -> TrafficShaper|None:
        return self._traffic_shaper

    @property
    def web_proxies(self) -> list[Proxy]:
        """Get the Web proxies to be used by programs in the zone (list may be empty)"""
        return self._web_proxies

    def get_proxy(self, proxy_ip_port:str) -> Proxy|None:
        for proxy in self._web_proxies:
            if f"{proxy.host}:{proxy.port}"==proxy_ip_port:
                return proxy
        return None

    @property
    def has_dns_resolution(self) -> bool:
        """Tell if the zone includes a DNS resolution service,
        either because it uses some resolvers or because it offers static resolution
        """
        return (self._network.resolvers is not None or self._network.resolv_rules is not None)

    @property
    def has_host_dns_resolvers(self) -> bool:
        """Tell if the zone uses the host's DNS resolvers configuration"""
        return self._network.resolvers == []

    @property
    def dns_resolvers(self) -> list[network.DNSEndpoint] | None:
        """Get the list of DNS resolvers:
        - None to not have any DNS resolution service, or only static resolution
        - [] (empty list) to use the host's resolvers
        - a non empty list to use a static list of DNS resolvers
        """
        return self._network.resolvers

    @property
    def in_fw_rules(self) -> list[network.FWRule] | None:
        return self._in_fw_rules

    @property
    def out_fw_rules(self) -> list[network.FWRule] | None:
        return self._network.fw_rules

    @property
    def out_resolv_rules(self) -> list[network.ResolvRule] | None:
        return self._network.resolv_rules


def load_adminns_file(
    config_dir: str,
    file_name: str,
    named_netres: dict[str, network.NetworkRessources] | None,
    traffic_shapers: dict[str, TrafficShaper]
) -> AdminNS:
    path = os.path.join(config_dir, file_name)
    with open(path, "r") as fd:
        return AdminNS.from_data(
            os.path.basename(path)[:-6], # remove .admzone extension
            json.load(fd),
            named_netres,
            traffic_shapers,
            config_dir,
        )
