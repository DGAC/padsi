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
from dataclasses import dataclass

import firewall

from .trafficshaper import TrafficShaper

_debug = False


class NetworkRessources:
    """Represent a list of network resources"""

    def __init__(self, descr: str, endpoints: list[firewall.Endpoint]):
        self._desrc = descr
        self._ipv4_endpoints: list[firewall.Endpoint] = []
        self._domain_endpoints: list[firewall.Endpoint] = []
        for ep in endpoints:
            for sep in ep.split_by_zone():
                if sep.is_ipv4:
                    self._ipv4_endpoints.append(sep)
                elif sep.is_wildcard_domain:
                    self._domain_endpoints.append(sep)
                else:
                    raise Exception(f"Unhandled not IPv4 or domain endpoint '{sep}'")

    @classmethod
    def from_data(cls, data: dict) -> NetworkRessources:
        descr = data.get("descr")
        if not isinstance(descr, str) or not descr:
            raise Exception("Missing or invalid 'descr' attribute in network resources data")
        endpoints = data.get("endpoints")
        if not isinstance(endpoints, list):
            raise Exception("Missing or invalid 'endpoints' attribute in network resources data")
        eplist: list[firewall.Endpoint] = []
        for eprepr in endpoints:
            try:
                eplist.append(firewall.Endpoint.from_repr(eprepr))
            except Exception as e:
                raise Exception(f"Invalid endpoint '{eprepr}' in network resources data: {str(e)}")

        return cls(descr, eplist)

    @property
    def descr(self):
        return self._desrc

    @property
    def ipv4_endpoints(self) -> list[firewall.Endpoint] | None:
        return self._ipv4_endpoints

    @property
    def domain_endpoints(self) -> list[firewall.Endpoint] | None:
        return self._domain_endpoints


def load_netres_file(path: str) -> dict[str, NetworkRessources]:
    """Load the contents of a .netres file"""
    res: dict[str, NetworkRessources] = {}
    with open(path, "r") as fd:
        data = json.load(fd)
        if not isinstance(data, dict):
            raise Exception(f"Invalid network resources file '{path}'")
        for name, netdata in data.items():
            if not isinstance(name, str) or not name:
                raise Exception(f"Invalid network resources name '{name}' in file '{path}'")
            if not isinstance(netdata, dict):
                raise Exception(f"Invalid network resources data for '{name}' in file '{path}'")
            try:
                res[name] = NetworkRessources.from_data(netdata)
            except Exception as e:
                raise Exception(f"{str(e)} (for '{name}' in file '{path}')")
    return res


class FWRuleChain(str, enum.Enum):
    INPUT = "INPUT"
    OUTPUT = "OUTPUT"
    FORWARD = "FORWARD"


@dataclass
class FWRule:
    """Represent a Firewall rule for trafic outound to the specified endpoint"""

    action: str  # "allow" or "deny"
    descr: str | None
    endpoint: firewall.Endpoint  # endpoint.is_ipv4 will be True
    chain: FWRuleChain = FWRuleChain.FORWARD

    def format_for_component(self) -> dict:
        return {
            "action": self.action,
            "rule": self.endpoint.to_repr(),
            "chain": self.chain.value,
        }


@dataclass
class ResolvRule:
    """Represent a domain resolution rule for trafic outound to the specified endpoint: the DNS server will
    resolve the domain and allow the associated IP(s). If the resolv attribute is not None, then the
    DNS server will resolve locally to the specified IP(s)
    """

    action: str  # "allow" or "deny"
    descr: str | None
    endpoint: firewall.Endpoint
    resolv: list[str] | None = None  # each string in the following format: 'A' '/' <response-validity> '/' <response as IPv4>

    def format_for_component(self) -> list[dict]:
        pr = self.endpoint.protocols_as_string
        pp = self.endpoint.ports_as_string
        if pr:
            spec = f"{pr}^{pp}" if pp else pr
        else:
            spec = pp if pp else None

        res: list[dict] = []
        for epzone in self.endpoint.zones:
            res.append(
                {
                    "action": self.action,
                    "query": epzone,
                    "reply": self.resolv,
                    "spec": spec,
                }
            )
        return res

    def is_part_of(self, other_endpoint: firewall.Endpoint) -> bool:
        return self.endpoint.is_part_of(other_endpoint)


def load_rules_from_data(rules_data: list[dict], named_netres: dict[str, NetworkRessources] | None) -> tuple[list[FWRule], list[ResolvRule]]:
    """Parse and load firewall and resolv. rules from some configuration data"""
    fw_rules: list[FWRule] = []
    resolv_rules: list[ResolvRule] = []

    if rules_data is not None:
        for rule in rules_data:
            if not isinstance(rule, dict):
                raise Exception(f"Invalid rule '{rule}'")
            action = rule.get("action")
            if action not in ("allow", "deny"):
                raise Exception(f"Invalid action in rule '{rule}'")
            descr = rule.get("descr")
            if descr is not None and not isinstance(descr, str):
                raise Exception(f"Invalid 'descr' attribute in rule '{rule}'")
            eprepr = rule.get("endpoint")
            netres = rule.get("netres")
            resolv = rule.get("resolv")
            if eprepr is not None and netres is not None:
                raise Exception(f"Invalid rule '{rule}': both 'endpoint' and 'netres' specified")
            if eprepr is None and netres is None:
                raise Exception(f"Invalid rule '{rule}': none of 'endpoint' or 'netres' specified")
            if eprepr is not None:
                if resolv is not None:
                    if not isinstance(resolv, list):
                        raise Exception(f"Invalid 'resolv' '{resolv}' attribute in '{rule}'")
                    for entry in resolv:
                        try:
                            (typ, ttl, ip4) = entry.split("/")
                            if typ == "A":
                                _ttl=int(ttl)
                                ipaddress.IPv4Address(ip4)
                            else:
                                raise Exception(f"unknown reply type '{typ}'")
                        except Exception as e:
                            raise Exception(f"Invalid 'resolv' '{resolv}' attribute in '{rule}': {str(e)}")
                try:
                    ep = firewall.Endpoint.from_repr(eprepr)
                    for sub_ep in ep.split_by_zone():
                        if sub_ep.is_ipv4 or sub_ep.is_all_ipv4:
                            if resolv is not None:
                                raise Exception("'resolv' attribute can't be specified for non domain endpoint")
                            fw_rules.append(FWRule(action, descr, sub_ep))
                        elif sub_ep.is_wildcard_domain:
                            resolv_rules.append(ResolvRule(action, descr, sub_ep, resolv))
                        else:
                            raise Exception(f"unhandled not IPv4 or domain endpoint '{sub_ep}'")
                except Exception as e:
                    raise Exception(f"Invalid 'endpoint' attribute '{eprepr}': {str(e)}")
            else:
                if named_netres is None:
                    raise Exception(f"Invalid rule '{rule}': 'netres' specified but no network resource defined")
                if netres is not None:
                    netresobj = named_netres.get(netres)
                    if netresobj is None:
                        raise Exception(f"Invalid rule '{rule}': network resource '{netres}' is not defined")
                    if netresobj.ipv4_endpoints is not None:
                        for ep in netresobj.ipv4_endpoints:
                            fw_rules.append(FWRule(action, netresobj.descr, ep))
                    if netresobj.domain_endpoints is not None:
                        for ep in netresobj.domain_endpoints:
                            resolv_rules.append(ResolvRule(action, netresobj.descr, ep))

    return (fw_rules, resolv_rules)


class DNSProtocol(str, enum.Enum):
    LEGACY = "LEGACY"
    DOT = "DOT"


@dataclass
class DNSEndpoint:
    ip_address: ipaddress.IPv4Address
    port: int
    protocol: DNSProtocol

    @classmethod
    def from_spec(cls, spec: str) -> DNSEndpoint:
        """Parse a string into a DNSSpec.
        The expected string format is: <IP address>[@<port>][@<protocol>]
        """
        try:
            port: int | None = None
            proto: DNSProtocol | None = None

            parts = spec.split("@")
            addr = ipaddress.IPv4Address(parts[0])
            if len(parts) > 1:
                for item in parts[1:]:
                    used = False
                    try:
                        port = int(item)
                        if port <= 0 or port > 65535:
                            raise Exception
                        used = True
                    except Exception:
                        pass

                    if not used:
                        try:
                            proto = DNSProtocol(item.upper())
                            used = True
                        except Exception:
                            pass
                    if not used:
                        raise Exception

            match (port, proto):
                case (None, None):
                    port = 53
                    proto = DNSProtocol.LEGACY
                case (853, None):
                    proto = DNSProtocol.DOT
                case (53, None):
                    proto = DNSProtocol.LEGACY
                case (None, DNSProtocol.DOT):
                    port = 853
                case (None, DNSProtocol.LEGACY):
                    port = 53
                case _:
                    pass

            return cls(addr, port, proto)  # pyright: ignore
        except Exception:
            raise Exception(f"Invalid DNS server specification '{spec}', expected <IP address>[@<port>][@<protocol>]")


class NetworkSpec:
    """Consolidated network configuration for a zone, a VM configured in a zone or a VM configuration"""

    def __init__(
        self,
        tshaper: TrafficShaper | None,
        resolvers: list[DNSEndpoint] | None,
        fw_rules: list[FWRule],
        resolv_rules: list[ResolvRule] | None,
    ):
        self._tshaper = tshaper
        self._resolvers = resolvers
        self._fw_rules: list[FWRule] = fw_rules
        self._resolv_rules: list[ResolvRule] | None = resolv_rules

    @property
    def traffic_shaper(self) -> TrafficShaper | None:
        return self._tshaper

    @property
    def resolvers(self) -> list[DNSEndpoint] | None:
        """List of configured DNS resolvers:
        - a non empty list or DNSEndpoint to use some specified DNS resolvers
        - None when no DNS resolution will be performed
        - an empty list to use the system's DNS resolvers
        """
        return self._resolvers

    @property
    def fw_rules(self) -> list[FWRule] | None:
        """Output firewall rules (rules directly implementable via the firewall)
        """
        return self._fw_rules

    @property
    def resolv_rules(self) -> list[ResolvRule] | None:
        """Output resolv rules (rules which rely on DNS resolution first)
        """
        return self._resolv_rules

    def specialize(self, data: dict | None, named_netres: dict[str, NetworkRessources] | None) -> NetworkSpec:
        """Create a new NetworkSpec object as a specialization of the current object"""
        if data is None:
            return self

        rules = data.get("out-rules")
        if rules is None:
            return self

        nss = NetworkSpec.from_data(data, named_netres, rules_only=True)
        copy = NetworkSpec(self._tshaper, self._resolvers, self._fw_rules, self._resolv_rules)
        if nss is not None:
            if nss._fw_rules is not None:
                copy._fw_rules = copy._fw_rules + nss._fw_rules if copy._fw_rules is not None else nss._fw_rules
            if nss._resolv_rules is not None:
                copy._resolv_rules = copy._resolv_rules + nss._resolv_rules if copy._resolv_rules is not None else nss._resolv_rules
        return copy

    @classmethod
    def from_data(
        cls,
        data: dict | None,
        named_netres: dict[str, NetworkRessources] | None,
        traffic_shapers: dict[str, TrafficShaper] | None = None,
        rules_only: bool = False,
        config_dir: str | None = None,
    ) -> NetworkSpec | None:
        if data is None:
            return None

        if not isinstance(data, dict):
            raise Exception(f"Invalid network definition {data}")

        tshaper: TrafficShaper | None = None
        resolvers:list[DNSEndpoint] | None=None

        if not rules_only:
            if config_dir is None:
                raise Exception("CODEBUG in NetworkSpec loading error: config_dir should not be None")
            # traffic shaper
            tshaper = None
            tsref = data.get("traffic-shaper")
            if tsref is not None and traffic_shapers is not None:
                try:
                    tshaper = traffic_shapers[tsref]
                except KeyError:
                    raise Exception(f"Unknown traffic shaper '{tsref}'")

            # DNS resolvers
            dresolvers = data.get("dns-resolvers")
            if dresolvers is not None:
                resolvers=[]
                if not isinstance(dresolvers, list):
                    raise Exception(f"Invalid list of DNS resolvers '{dresolvers}'")
                for item in dresolvers:
                    resolvers.append(DNSEndpoint.from_spec(item))

        # firewall and resolv. rules
        (fw_rules, resolv_rules) = load_rules_from_data(data.get("out-rules", []), named_netres)

        return cls(tshaper, resolvers, fw_rules, resolv_rules)
