#!/usr/bin/python3

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


import re
import unittest

import firewall.netflow as netflow


def _domain_match(domain: str, rule: str) -> bool:
    regex = netflow.domain_to_regex(rule)
    expr = re.compile(regex)
    return re.match(expr, domain) is not None


class MiscTest(unittest.TestCase):
    def test_domain(self):
        self.assertFalse(netflow._is_domain_name(""))
        self.assertFalse(netflow._is_domain_name("."))
        self.assertTrue(netflow._is_domain_name("aaa."))
        self.assertFalse(netflow._is_domain_name("aaa.bbb")) # no '.' at the end
        self.assertTrue(netflow._is_domain_name("aaa.bbb."))

    def test_wildcard_domain(self):
        self.assertFalse(netflow._is_domain_name("*", allow_wildcards=True)) # no '.' at the end
        self.assertFalse(netflow._is_domain_name("**", allow_wildcards=True)) # no '.' at the end
        self.assertTrue(netflow._is_domain_name("**.", allow_wildcards=True))
        self.assertTrue(netflow._is_domain_name("*.", allow_wildcards=True))
        self.assertFalse(netflow._is_domain_name("a.**.", allow_wildcards=True))

        self.assertTrue(_domain_match("abc", "*"))
        self.assertFalse(_domain_match("", "*"))
        self.assertTrue(_domain_match("z", "*z"))
        self.assertFalse(_domain_match("abc.", "*"))
        self.assertFalse(_domain_match("abc.", "*.com"))
        self.assertTrue(_domain_match("abc.com", "*.com"))
        self.assertFalse(_domain_match("ab*c.com", "*.com"))
        self.assertFalse(
            _domain_match(
                ".com",
                "*.com",
            )
        )
        self.assertTrue(_domain_match("bc.com", "*bc.com"))
        self.assertTrue(_domain_match("abc.com", "*bc.com"))
        self.assertTrue(_domain_match("abc.com", "*bc.com"))
        self.assertTrue(_domain_match("bc.com.", "**bc.com."))
        self.assertTrue(_domain_match("abc.com.", "**bc.com."))
        self.assertTrue(_domain_match(".abc.com.", "**bc.com."))
        self.assertTrue(_domain_match("ww.abc.com.", "**bc.com."))

    def test_domain_part(self):
        self.assertTrue(netflow._domain_is_part_of("*.debian.pool.ntp.org.", "**.ntp.org."))
        self.assertTrue(netflow._domain_is_part_of("example.com", "example.com"))
        self.assertFalse(netflow._domain_is_part_of("example.com", "*.example.com"))
        self.assertTrue(netflow._domain_is_part_of("example.com", "*example.com"))
        self.assertTrue(netflow._domain_is_part_of("someexample.com", "*example.com"))
        self.assertFalse(netflow._domain_is_part_of("some.example.com", "*example.com"))
        self.assertTrue(netflow._domain_is_part_of("www.example.com", "**example.com"))
        self.assertTrue(netflow._domain_is_part_of("www.someexample.com", "**example.com"))
        self.assertTrue(netflow._domain_is_part_of("a.www.example.com", "**example.com"))

        self.assertTrue(netflow._domain_is_part_of("*.example.com", "*.example.com"))
        self.assertTrue(netflow._domain_is_part_of("**example.com", "**example.com"))
        self.assertTrue(netflow._domain_is_part_of("**someexample.com", "**example.com"))
        self.assertFalse(netflow._domain_is_part_of("**ample.com", "**example.com"))
        self.assertTrue(netflow._domain_is_part_of("*.example.com", "**example.com"))
        self.assertTrue(netflow._domain_is_part_of("*.www.example.com", "**example.com"))
        self.assertTrue(netflow._domain_is_part_of("*.www.someexample.com", "**example.com"))

        self.assertTrue(netflow._domain_is_part_of("*w.example.com", "**.example.com"))
        self.assertTrue(netflow._domain_is_part_of("*.example.com", "**.example.com"))
        self.assertFalse(netflow._domain_is_part_of("**.web.example2.com", "**.example.com"))
        self.assertTrue(netflow._domain_is_part_of("**.web.example.com", "**.example.com"))

        self.assertFalse(netflow._domain_is_part_of("*.example.com", "w*.example.com"))
        self.assertTrue(netflow._domain_is_part_of("w*.example.com", "w*.example.com"))
        self.assertTrue(netflow._domain_is_part_of("www.example.com", "w*.example.com"))
        self.assertTrue(netflow._domain_is_part_of("w.example.com", "w*.example.com"))


class EndpointTest(unittest.TestCase):
    def test_invalid(self):
        with self.assertRaises(Exception):
            netflow.Endpoint.from_repr("**")
        with self.assertRaises(Exception):
            netflow.Endpoint(protocols="sctp, tcp")
        with self.assertRaises(Exception):
            netflow.Endpoint(protocols="tcp", ports="0")
        with self.assertRaises(Exception):
            netflow.Endpoint(protocols="tcp", ports="65536")
        with self.assertRaises(Exception):
            netflow.Endpoint(protocols="tcp", ports="-10")

    def test_valid(self):
        ep = netflow.Endpoint()
        r = repr(ep)
        self.assertEqual(r, "*")
        ep2 = netflow.Endpoint.from_repr(r)
        self.assertEqual(ep, ep2)

        ep = netflow.Endpoint(protocols="tcp")
        r = repr(ep)
        self.assertEqual(r, "*^tcp")
        ep2 = netflow.Endpoint.from_repr(r)
        self.assertEqual(ep, ep2)

        ep = netflow.Endpoint(zones="*", protocols="tcp")
        r = repr(ep)
        self.assertEqual(r, "*^tcp")
        ep2 = netflow.Endpoint.from_repr(r)
        self.assertEqual(ep, ep2)

        ep = netflow.Endpoint(protocols="tcp", ports="1,445-446,567-678")
        r = repr(ep)
        self.assertEqual(r, "*^tcp^1,445-446,567-678")
        ep2 = netflow.Endpoint.from_repr(r)
        self.assertEqual(ep, ep2)

        ep = netflow.Endpoint(protocols="tcp", zones="172.16.0.0/16", ports="3389")
        r = repr(ep)
        self.assertEqual(r, "172.16.0.0/16^tcp^3389")
        ep2 = netflow.Endpoint.from_repr(r)
        self.assertEqual(ep, ep2)
        ep3 = netflow.Endpoint.from_repr("172.16.0.0/16 ^ tcp ^ 3389")
        self.assertEqual(ep, ep3)

        ep = netflow.Endpoint(
            protocols="tcp,udp", zones="172.16.0.0/16,#eth0", ports="3389,1-65535"
        )
        r = repr(ep)
        self.assertEqual(r, "172.16.0.0/16,#eth0^tcp,udp^3389,1-65535")
        ep2 = netflow.Endpoint.from_repr(r)
        self.assertEqual(ep, ep2)
        ep3 = netflow.Endpoint.from_repr(
            "  172.16.0.0/16   ,     #eth0 ^   tcp,   udp ^  3389,   1-65535     "
        )
        self.assertEqual(ep, ep3)

    def test_with_name(self):
        for s in ("example.com.", "www.example.com."):
            ep = netflow.Endpoint(zones=s)
            r = repr(ep)
            self.assertEqual(r, s)
            ep2 = netflow.Endpoint.from_repr(r)
            self.assertEqual(ep, ep2)

        ep = netflow.Endpoint(protocols="tcp", zones="security.debian.org.", ports="443,80")
        r = repr(ep)
        self.assertEqual(r, "security.debian.org.^tcp^443,80")
        ep2 = netflow.Endpoint.from_repr(r)
        self.assertEqual(ep, ep2)


class NetFlowTest(unittest.TestCase):
    def test_valid(self):
        ep1 = netflow.Endpoint()
        ep2 = netflow.Endpoint(zones="192.168.200.4", protocols="icmp, udp")
        fl = netflow.NetFlow(ep1, ep2)
        r = repr(fl)
        self.assertEqual(r, "*>icmp,udp>192.168.200.4")
        fl2 = netflow.NetFlow.from_repr(r)
        self.assertEqual(fl, fl2)
        fl3 = netflow.NetFlow.from_repr("     * > icmp,udp > 192.168.200.4 ")
        self.assertEqual(fl, fl3)

    def test_repr(self):
        netflow.NetFlow.from_repr("*>>*")
        netflow.NetFlow.from_repr("*>tcp>*^443")
        netflow.NetFlow.from_repr("*>tcp>34.117.59.81,#tap0^443")
        netflow.NetFlow.from_repr("*>>34.117.59.81,#tap0^443")
        netflow.NetFlow.from_repr("*>>192.168.244.2")

    def test_split_proto(self):
        fl = netflow.NetFlow.from_repr("*>tcp>1.1.1.1^443")
        sl = fl.split_by_protocol()
        self.assertEqual(sl, {"tcp": fl})

        fl = netflow.NetFlow.from_repr("*>tcp,udp>1.1.1.1^443")
        sl = fl.split_by_protocol()
        self.assertEqual(
            sl,
            {
                "tcp": netflow.NetFlow.from_repr("*>tcp>1.1.1.1^443"),
                "udp": netflow.NetFlow.from_repr("*>udp>1.1.1.1^443"),
            },
        )

        fl = netflow.NetFlow(
            netflow.Endpoint.from_repr("*^tcp^443,80"),
            netflow.Endpoint.from_repr("*^tcp^221"),
        )
        sl = fl.split_by_protocol()
        self.assertEqual(sl, {"tcp": fl})


if __name__ == "__main__":
    unittest.main()
