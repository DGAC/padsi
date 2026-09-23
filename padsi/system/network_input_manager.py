#
# Copyright (c) 2026 DGAC/DSNA
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

import syslog

from padsi import fwlib, network
from padsi.config import FWRule

_debug = False


class NetworkInputManager:
    """Object to concentrate incoming rules and adapt which network interfaces for the default route change"""

    def __init__(self, fw: fwlib.Firewall):
        self._fw = fw
        self._default_route_ifaces: set[str] = set()
        self._rules: dict[str, FWRule] = {}

    def add_rule(self, rule: FWRule):
        """Add an accept FW rule"""
        if _debug:
            syslog.syslog(syslog.LOG_DEBUG, f"NetworkInputManager::add_rule({rule})")
        rule_id = str(rule)
        if rule_id in self._rules:
            return

        self._rules[rule_id] = rule

        for iface in self._default_route_ifaces:
            ep = fwlib.Endpoint.from_repr(f"#{iface}")
            flow = fwlib.NetFlow(ep, rule.endpoint)
            self._fw.flow_set_policy(
                fwlib.FlowType.FILTER_INPUT,
                flow,
                fwlib.Policy.ALLOW
                if rule.action == "allow"
                else fwlib.Policy.DENY,
            )

    def declare_default_route_interface(self, iface: str):
        """Declare an interface as used by a default route in the namespace of the traffic shaper
        Does nothing if interface is already declared
        """
        if iface in self._default_route_ifaces:
            return
        self._default_route_ifaces.add(iface)
        for rule in self._rules.values():
            if _debug:
                syslog.syslog(
                    syslog.LOG_DEBUG,
                    f"NetworkInputManager::declare_default_route_interface({iface=} {rule=}",
                )
            ep = fwlib.Endpoint.from_repr(f"#{iface}")
            flow = fwlib.NetFlow(ep, rule.endpoint)
            self._fw.flow_set_policy(
                fwlib.FlowType.FILTER_INPUT,
                flow,
                fwlib.Policy.ALLOW
                if rule.action == "allow"
                else fwlib.Policy.DENY,
            )

    def undeclare_default_route_interface(self, iface: str):
        """Un-declare an interface as used by a default route in the namespace of the traffic shaper
        Does nothing if interface is not already declared
        """
        if iface not in self._default_route_ifaces:
            return
        self._default_route_ifaces.remove(iface)
        if _debug:
            syslog.syslog(
                syslog.LOG_DEBUG,
                f"NetworkInputManager::undeclare_default_route_interface({iface=})",
            )
        if _debug:
            self._fw.clear_interface_rules(iface, fwlib.FlowType.FILTER_INPUT)

    async def adapt(self):
        default_route_ifaces = network.get_default_interfaces()
        for iface in default_route_ifaces:
            self.declare_default_route_interface(iface)
        clist = self._default_route_ifaces.copy()
        for iface in clist:
            if iface not in default_route_ifaces:
                self.undeclare_default_route_interface(iface)
        self._default_route_ifaces = default_route_ifaces
