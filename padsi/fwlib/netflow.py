#
# Copyright (c) 2025-2026 DGAC/DSNA
# Copyright (c) 2024 Vivien Malerba <vmalerba@gmail.com>
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

import copy
import ipaddress
import re
from collections import Counter


class SubNewFlowDifferencesException(Exception):
    """Raised when a NetFlow is actually made of several sub-flows and some
    have different characteristics than others"""
    pass


_all_protocols = ("tcp", "udp", "icmp")


def _validate_protocol(proto: str):
    if proto not in _all_protocols:
        raise Exception(f"invalid protocol {proto}")


def _validate_network_interface(iface: str):
    # NB: only a very limited check here, the real check will be done at usage
    if not isinstance(iface, str) or len(iface) > 15:
        raise Exception(f"invalid network interface {iface}")


def _validate_port(port: int):
    if not isinstance(port, int) or port < 1 or port > 65535:
        raise Exception(f"invalid port {port}")


def _is_ipv4_element(item: str) -> bool:
    """Tell if a string represents an IPv4 address or network"""
    try:
        ipaddress.IPv4Address(item)
        return True
    except Exception:
        try:
            ipaddress.IPv4Network(item)
            return True
        except Exception:
            return False


_domain_regex_expr = r"^(([\da-zA-Z])([_\w-]{,62})\.){,127}(([\da-zA-Z])[_\w-]{,61})?([\da-zA-Z]\.((xn\-\-[a-zA-Z\d]+)|([a-zA-Z\d]{2,})))\.?$"
_domain_regex = re.compile(_domain_regex_expr, re.IGNORECASE)
_single_regex_expr = r"^([\da-zA-Z])([_\w-]{,62})\.?$"
_single_regex = re.compile(_single_regex_expr, re.IGNORECASE)
def _is_domain_name(item: str, allow_wildcards: bool = False) -> bool:
    """Return True if a string represents a valid domain name (and not an IP address)
    Note: always return False if the item does not end with a '.'
    """
    if not item or item[-1]!=".":
        return False

    if _is_ipv4_element(item):
        return False
    if allow_wildcards:
        if item in ("*", "**"):
            return True

        # ** is only allowed at the start
        if item.startswith("**"):
            if "**" in item[2:]:
                return False
        elif "**" in item:
            return False

        d = item.replace("**", "aaa.bbb").replace("*", "ccc")
        return _is_domain_name(d, allow_wildcards=False)
    else:
        if re.match(_single_regex, item):
            return True
        return bool(re.match(_domain_regex, item))


def domain_to_regex(domain: str) -> str:
    """Create a Regex from a wildcard domain"""
    if "*" in domain:
        # tmp replace ** with § to avoid confusing the next modifications
        q = domain.replace("**", "§")
        # handle each "label" independantly
        parts = q.split(".")
        nparts = []
        for p in parts:
            if p == "*":
                nparts.append(r"[^\.\*]+")  # at least one character
            else:
                nparts.append(p.replace("*", r"[^\.\*]*"))  # zero or more characters

        # don't interpret the dot character as a regex placeholder
        q = r"\.".join(nparts)

        # convert back § to "at least one character", and add start and end markers
        return "^" + q.replace("§", ".*") + "$"
    return domain.replace(".", r"\.")


def _domain_is_part_of(domain: str, other_domain: str) -> bool:
    if "*" in domain:
        if "*" in other_domain:
            if "**" in domain:
                if domain.startswith("**"):
                    if other_domain.startswith("**"):
                        return _domain_is_part_of(domain[1:], other_domain)
                    else:
                        return False
            else:
                rd = domain.replace("*", "abc")
                return re.match(domain_to_regex(other_domain), rd) is not None
        return False
    else:
        if "*" in other_domain:
            return re.match(domain_to_regex(other_domain), domain) is not None
        return domain == other_domain


def _deep_list_equals(l1: list | None, l2: list | None) -> bool:
    """Compare two lists where:
    - each list can be None or not
    - without taking care of the elements' order in the lists
    """
    if l1 is None:
        if l2 is None:
            return True
        return False
    if l2 is None:
        return False
    return Counter(l1) == Counter(l2)

def analyse_protocol_spec(protocol_spec:str|None) -> list[str] | None:
    protocols: list[str] | None = []
    if protocol_spec is not None:
        protocols = []
        for item in protocol_spec.strip().split(","):
            item = item.strip()
            if item:
                _validate_protocol(item)
                protocols.append(item)
    if len(protocols) == 0:
        protocols = None
    return protocols

def analyse_port_spec(port_spec:str|None) -> tuple[list[int]|None, list[str]|None]:
    ports: list[int] | None = []
    port_ranges: list[str] | None = []
    if port_spec is not None:
        if isinstance(port_spec, int):
            _validate_port(port_spec)
            ports.append(port_spec)
        else:
            for item in port_spec.strip().split(","):
                item = item.strip()
                if "-" in item:
                    try:
                        (p1, p2) = item.split("-")
                        p1 = int(p1)
                        p2 = int(p2)
                        if p1 < 1 or p1 > 65535 or p2 < 1 or p2 > 65535 or p1 > p2:
                            raise
                        if p1 == p2:
                            ports.append(p1)
                        else:
                            port_ranges.append(item)
                    except Exception:
                        raise Exception(f"Invalid endpoint spec: invalid port range '{item}'")
                else:
                    try:
                        port = int(item)
                        _validate_port(port)
                        ports.append(port)
                    except Exception:
                        raise Exception(f"invalid port number '{item}'")

    if len(ports) == 0:
        ports = None
    if len(port_ranges) == 0:
        port_ranges = None
    return (ports, port_ranges)

class Endpoint:
    def __init__(
        self,
        zones: str | None = None,
        protocols: str | None = None,
        ports: str | None = None,
    ):
        """Define a net flow endpoint (source or destination)
        Refer to the the flow-grammar.txt file for the formats
        """
        # protocols
        self._protocols=analyse_protocol_spec(protocols)

        # Zones
        self._zones: list[str] = []
        self._iface = None
        if zones is not None:
            data = zones.strip()
            for item in data.split(","):
                item = item.strip()
                if item=="":
                    raise Exception("Invalid empty (\"\") endpoint spec")
                elif item=="*":
                    self._zones.append(item)
                elif item[0] == "#":
                    _validate_network_interface(item[1:])
                    if self._iface:
                        raise Exception("Invalid endpoint spec: more than one interface specified")
                    self._iface = item[1:]
                else:
                    if item.endswith("/32"):
                        # we have an IP address, but ipaddress.IPv4Address won't like it, so strip the "/32"
                        item = item[:-3]
                    try:
                        self._zones.append(str(ipaddress.IPv4Address(item)))
                    except Exception:
                        try:
                            self._zones.append(str(ipaddress.IPv4Interface(item)))
                        except Exception:
                            if _is_domain_name(item, allow_wildcards=True):
                                self._zones.append(item)
                            else:
                                if item[-1]==".":
                                    raise Exception(f"Invalid endpoint spec '{item}'")
                                raise Exception(f"Invalid endpoint spec '{item}' (missing final dot)")
        if len(self._zones)==0 and self._iface is None:
            self._zones=["*"]

        # ports
        (self._ports, self._port_ranges)=analyse_port_spec(ports)

    @property
    def protocols(self) -> list[str] | None:
        # NB: returns None or a list with at least one element
        return self._protocols

    def add_protocol(self, proto):
        """Add a new protocol"""
        _validate_protocol(proto)
        if self._protocols is None:
            self._protocols = [proto]
        elif proto not in self._protocols:
            self._protocols.append(proto)

    @property
    def interface(self) -> str | None:
        return self._iface

    @interface.setter
    def interface(self, interface: str):
        _validate_network_interface(interface)
        if self._iface is None:
            self._iface = interface
            if self._zones==["*"]:
                self._zones=[]
        elif self._iface == interface:
            pass
        else:
            raise Exception("endpoint already has a declared network interface")

    @property
    def zones(self) -> list[str]:
        """Get all the zones of the endpoint"""
        return self._zones

    @property
    def is_ipv4(self) -> bool:
        """Tell if the endpoint specified only one or more IPv4 addresses"""
        for zone in self._zones:
            if not _is_ipv4_element(zone):
                return False
        return len(self._zones) > 0

    @property
    def is_all_ipv4(self) -> bool:
        """Tell if the endpoint is the equivalent of 0.0.0.0/0"""
        return self._zones==["*"]

    @property
    def ipv4_zones(self) -> list[str]:
        """Get the zones which are IPv4 addresses"""
        res = []
        for zone in self._zones:
            if _is_ipv4_element(zone):
                res.append(zone)
        return res

    @property
    def ipv4_zones_map(self) -> set[ipaddress.IPv4Address | ipaddress.IPv4Network]:
        """Get all the IPv4 addresses or networks"""
        res = set()
        for zone in self._zones:
            if _is_ipv4_element(zone):
                try:
                    res.add(ipaddress.IPv4Address(zone))
                except Exception:
                    try:
                        res.add(ipaddress.IPv4Network(zone))
                    except Exception:
                        raise Exception(f"TODO: unhandled address element '{zone}'")
        return res

    @property
    def is_domain(self) -> bool:
        """Tell if the endpoint specified only one or more domain addresses"""
        for zone in self._zones:
            if not _is_domain_name(zone):
                return False
        return len(self._zones) > 0

    @property
    def is_wildcard_domain(self) -> bool:
        """Tell if the endpoint specified only one or more domain addresses including wildcards"""
        for zone in self._zones:
            if not _is_domain_name(zone, allow_wildcards=True):
                return False
        return len(self._zones) > 0

    @property
    def domain_zones(self) -> list[str]:
        """Get the zones which are domains"""
        res = []
        for zone in self._zones:
            if _is_domain_name(zone, allow_wildcards=True):
                res.append(zone)
        return res

    def add_address(self, addr: ipaddress.IPv4Interface | ipaddress.IPv4Address):
        """Add an address as a ipaddress.IPv4Interface or ipaddress.IPv4Address to the
        zones part of the endpoint
        """
        # for /32 networks, we want to use an ipaddress.IPv4Address
        if isinstance(addr, ipaddress.IPv4Interface) and addr.network.prefixlen == 32:
            addr = addr.ip
        saddr = str(addr)
        if saddr not in self._zones:
            if self._zones==["*"]:
                self._zones=[saddr]
            else:
                self._zones.append(saddr)

    @property
    def ports(self) -> list[int] | None:
        if self._ports is not None and self._protocols is None:
            raise Exception("Invalid endpoint: ports specified without any protocol")
        return self._ports

    def add_port(self, port: int):
        _validate_port(port)
        if self._ports is None:
            self._ports = [port]
        elif port not in self._ports:
            self._ports.append(port)

    @property
    def port_ranges(self) -> list[str] | None:
        if self._port_ranges is not None and self._port_ranges is None:
            raise Exception(
                "Invalid endpoint: ports range specified without any protocol"
            )
        return self._port_ranges

    def add_portrange(self, p1: int, p2: int):
        _validate_port(p1)
        _validate_port(p2)
        if p1 > p2:
            raise Exception(f"Invalid endpoint spec: invalid port range {p1}-{p2}")
        elif p1 == p2:
            self.add_port(p1)
        else:
            text = f"{p1}-{p2}"
            if self._port_ranges is None:
                self._port_ranges = [text]
            elif text not in self._port_ranges:
                self._port_ranges.append(text)

    @property
    def ports_map(self) -> set[int]:
        """Actual list of all the ports, as a set"""
        if self._ports is None and self._port_ranges is None:
            return set(range(1, 65536))

        res = set()
        if self._ports is not None:
            for port in self._ports:
                res.add(port)
        if self._port_ranges is not None:
            for item in self._port_ranges:
                try:
                    (s, e) = [int(p) for p in item.split("-")]
                    _validate_port(s)
                    _validate_port(e)
                    if s > e:
                        raise Exception()
                    for port in range(s, e + 1):
                        res.add(port)
                except Exception:
                    raise Exception(f"Invalid port range '{item}'")
        return res

    @property
    def protocols_as_string(self) -> str | None:
        if self._protocols is not None:
            return ",".join(self._protocols)
        return None

    @property
    def ports_as_string(self) -> str | None:
        plist = []
        if self._ports is not None:
            for port in self._ports:
                plist.append(str(port))
        if self._port_ranges is not None:
            for pr in self._port_ranges:
                plist.append(pr)
        return ",".join(plist) if len(plist) > 0 else None

    def to_repr(self, include_protocols=True):
        parts = []
        # zones part
        zones = []
        for net in self._zones:
            zones.append(net)
        if self._iface is not None:
            zones.append(f"#{self._iface}")
        if len(zones) > 0:
            parts.append(",".join(zones))
        else:
            parts.append("*")

        if include_protocols:
            # protocols
            prepr = self.protocols_as_string
            if prepr is not None:
                parts.append(f"^{prepr}")

        # ports
        prepr = self.ports_as_string
        if prepr is not None:
            parts.append(f"^{prepr}")

        return "".join(parts)

    @classmethod
    def from_repr(cls, txt: str, protos: str | None = None) -> Endpoint:
        """Create an EndPoint instance from its textual representation"""
        txt = txt.strip()
        if txt == "":
            raise Exception("Invalid endpoint empty spec.")
        parts = txt.split("^")
        if len(parts) > 3:
            raise Exception(f"Invalid endpoint spec. {txt}")
        if protos is None:
            protocols = parts[1] if len(parts) > 1 else None
            ports = parts[2] if len(parts) > 2 else None
        else:
            if len(parts) == 3:
                protocols = parts[1]
                ports = parts[2]
            else:
                protocols = protos
                ports = parts[1] if len(parts) > 1 else None
        return cls(zones=parts[0], protocols=protocols, ports=ports)

    def __repr__(self):
        return self.to_repr()

    def is_equal_to(self, other: Endpoint, include_protocols=True) -> bool:
        if self.interface != other.interface or \
            not _deep_list_equals(self._zones, other._zones) or \
            not _deep_list_equals(self._ports, other._ports) or \
            not _deep_list_equals(self._port_ranges, other._port_ranges):
            return False
        if include_protocols and not _deep_list_equals(self._protocols, other._protocols):
            return False
        return True

    def __eq__(self, other: object) -> bool:
        return self.is_equal_to(other)  # pyright: ignore

    def split_by_protocol(self) -> dict[str | None, Endpoint]:
        """Split the endpoint by creating (or reusing) one or more EndPoint objects, one by protocol (and one if this endpoint
        does not specify a protocol)

        Returns a dictionary indexed by protocols where values are EndPoint objects. If this endpoint object does
        not specify a protocol, the the single key in the dictionary is None
        """
        if self._protocols is None:
            return {None: self}
        if len(self._protocols) == 1:
            return {self._protocols[0]: self}
        res = {}
        for p in self._protocols:
            c = copy.deepcopy(self)
            c._protocols = [p]
            res[p] = c
        return res

    def split_by_zone(self) -> list[Endpoint]:
        """Split the endpoint into one or more endpoints each with a unique zone"""
        if len(self._zones) == 1:
            return [self]

        eplist = []
        for zone in self._zones:
            eplist.append(Endpoint(zones=zone, protocols=None if self._protocols is None else ",".join(self._protocols), ports=self.ports_as_string))
        return eplist

    def is_part_of(self, other: Endpoint) -> bool:
        """Tell if the current endpoint is part of of the specified superset endpoint"""
        # interface
        if self.interface is None:
            if other.interface is not None:
                return False
        elif other.interface is not None and self.interface != other.interface:
            return False

        # protocols
        this_data = (
            self.protocols
            if self.protocols is not None and len(self.protocols) > 0
            else _all_protocols
        )
        super_data = (
            other.protocols
            if other.protocols is not None and len(other.protocols) > 0
            else _all_protocols
        )
        for proto in this_data:
            if proto not in super_data:
                return False

        # zones, IP addresses
        super_ip_zones = other.ipv4_zones_map
        for item in self.ipv4_zones_map:
            found = False
            if isinstance(item, ipaddress.IPv4Address):
                for sitem in super_ip_zones:
                    if item == sitem or isinstance(sitem, ipaddress.IPv4Network) and item in sitem:
                        found = True
                        break
            elif isinstance(item, ipaddress.IPv4Network):
                for sitem in super_ip_zones:
                    if isinstance(sitem, ipaddress.IPv4Network) and item.subnet_of(sitem):
                        found = True
                        break
            else:
                raise Exception(f"Unhandled ip element '{item}'")
            if not found:
                return False

        # zones, domains (with or without wildcards)
        super_domain_zones = other.domain_zones
        for domain in self.domain_zones:
            found = False
            for sdomain in super_domain_zones:
                if _domain_is_part_of(domain, sdomain):
                    found = True
                    break
            if not found:
                return False

        # ports
        this_data = self.ports_map
        super_data = other.ports_map
        for port in this_data:
            if port not in super_data:
                return False

        return True


class NetFlow:
    """Represents a subset of flow specification on which we can
    set up some firewall rules
    Cf. the flow-grammar.txt file for the formats

    NB: each Endpoint has its own protocols property, but in the context of a net flow, the two
        must be either identical or None in one of the endpoints. So comparisons and repr() of endpoints
        when used within a NetFlow objects don't take into account the protocols property which is
        managed by the NetFlow object itself.
    """

    def __init__(self, src: Endpoint | None, dest: Endpoint | None):
        self._src = src if src is not None else Endpoint()
        self._dest = dest if dest is not None else Endpoint()
        self._check_protocols()

    def _check_protocols(self):
        sps = self._src.protocols
        dps = self._dest.protocols
        if sps is not None and dps is not None:
            if Counter(sps) != Counter(dps):
                raise Exception(
                    f"Source '{','.join(sps)}' and destination '{','.join(dps)}' protocols don't match"
                )

    def __repr__(self):
        protocols = self.protocols
        pstr = ",".join(protocols) if protocols is not None else ""
        return f"{self._src.to_repr(include_protocols=False)}>{pstr}>{self._dest.to_repr(include_protocols=False)}"

    def __eq__(self, other) -> bool:
        if self.protocols != other.protocols:
            return False
        return self.src.is_equal_to(other.src, include_protocols=False) and \
            self.dest.is_equal_to(other.dest, include_protocols=False)

    @property
    def src(self) -> Endpoint:
        return self._src

    @property
    def dest(self) -> Endpoint:
        return self._dest

    @property
    def protocols(self) -> list[str] | None:
        self._check_protocols()
        if self.src.protocols is not None:
            return self.src.protocols
        return self.dest.protocols

    @classmethod
    def from_repr(cls, txt: str) -> NetFlow:
        """Create a NetFlow instance from its textual representation"""
        try:
            (rsrc, protocols, rdest) = txt.split(">")
        except Exception:
            raise Exception("Invalid endpoint spec: no '>' separator")

        src = Endpoint.from_repr(rsrc, protos=protocols)
        dest = Endpoint.from_repr(rdest, protos=protocols)
        if protocols:
            for proto in protocols.split(","):
                proto=proto.strip()
                if proto:
                    src.add_protocol(proto)
                    dest.add_protocol(proto)
        return cls(src, dest)

    def split_by_protocol(self) -> dict[str | None, NetFlow]:
        """Split the flow by creating (or reusing) one or more NetFlow objects, one by protocol (and one if this netflow
        does not specify a protocol)

        Returns a dictionary indexed by protocols where values are NetFlow objects. If this netflow object does
        not specify a protocol, the the single key in the dictionary is None
        """
        protos = self.protocols

        if protos is None:
            return {None: self}
        if len(protos) == 1:
            return {protos[0]: self}

        split_src = self._src.split_by_protocol()
        split_dest = self._dest.split_by_protocol()

        flows = {}
        for p in protos:
            ep_src = split_src[p] if p in split_src else split_src[None]
            ep_dest = split_dest[p] if p in split_dest else split_dest[None]
            flows[p] = NetFlow(ep_src, ep_dest)
        return flows
