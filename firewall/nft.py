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

import ipaddress
import json
import subprocess
import syslog
import time
from typing import Any

from . import netflow, protocols
from .common import Family, FlowType, LogSpec, Policy

_log_commands=False
_match_debug=False


def _get_table_and_chain_from_flow(flowtype:FlowType, prefix:str|None) -> tuple[str, str]:
    if prefix is None:
        return flowtype.value.split(".") # pyright: ignore
    (table, chain)=flowtype.value.split(".")
    return (f"{prefix}_{table}", f"{prefix}_{chain}")

def _netflow_to_nft_args(nfl:netflow.NetFlow) -> list[str]:
    args=[]
    protos=nfl.protocols
    if protos is not None:
        if len(protos)>1:
            raise Exception("CODEBUG: more than one protocol!")
        args+=["ip", "protocol", protos[0]]
    if nfl.src.interface is not None:
        args+=["meta", "iif", nfl.src.interface]
    if nfl.dest.interface is not None:
        args+=["meta", "oif", nfl.dest.interface]
    if nfl.src.zones and not nfl.src.is_all_ipv4:
        args+=["ip", "saddr", "{"+",".join(nfl.src.zones)+"}"]
    if nfl.dest.zones and not nfl.dest.is_all_ipv4:
        args+=["ip", "daddr", "{"+",".join(nfl.dest.zones)+"}"]

    if nfl.src.ports is not None or nfl.src.port_ranges is not None:
        ports=[]
        if nfl.src.ports is not None:
            ports=[str(p) for p in nfl.src.ports]
        if nfl.src.port_ranges is not None:
            ports+=nfl.src.port_ranges
        if protos is not None:
            for p in protos:
                if p not in ("tcp", "udp"):
                    raise Exception("source or destination port can only be specified for TCP or UDP protocols")
                args+=[p, "sport", "{"+",".join([str(p) for p in ports])+"}"]

    if nfl.dest.ports is not None or nfl.dest.port_ranges is not None:
        ports=[]
        if nfl.dest.ports is not None:
            ports=[str(p) for p in nfl.dest.ports]
        if nfl.dest.port_ranges is not None:
            ports+=nfl.dest.port_ranges
        if protos is not None:
            for p in protos:
                if p not in ("tcp", "udp"):
                    raise Exception("source or destination port can only be specified for TCP or UDP protocols")
                args+=[p, "dport", "{"+",".join([str(p) for p in ports])+"}"]
    return args

def _find_table(data:dict, table_name:str, family:Family=Family.IPv4) -> int|None:
    """Find a table in the specified data
    Return: the table handle in the data if found and None if not found
    """
    for item in data["nftables"]:
        if "table" in item:
            data=item["table"]
            if data.get("name")==table_name and data.get("family")==family.value:
                return data["handle"]
    return None

def _find_chain(data:dict, table_name:str, chain_name:str, family:Family=Family.IPv4) -> tuple[int|None, Policy|None]:
    """Find a chain in the specified data
    Return: the chain handle in the data if found and the default policy, or (None, None) if not found
    """
    for item in data["nftables"]:
        if "chain" in item:
            data=item["chain"]
            if data.get("table")==table_name and data.get("family")==family.value and data.get("name")==chain_name:
               return (data["handle"], Policy.from_keyword(data["policy"]))
    return (None, None)

def _addr_to_object(data:Any) -> ipaddress.IPv4Interface:
    """Convert a "right" or "left" address argument to the correct object
    """
    # simple address?
    if isinstance(data, str):
        return ipaddress.IPv4Interface(data)
    # like {'prefix': {'addr': '192.168.120.0', 'len': 24}}} ?
    if isinstance(data, dict) and "prefix" in data:
        addrdata=data["prefix"]
        if "addr" in addrdata and "len" in addrdata:
            return ipaddress.IPv4Interface(f"{addrdata['addr']}/{addrdata['len']}")
    raise Exception(f"Unhandled nft json's address formatted as: {data}")

def _match_netflow(nflow:netflow.NetFlow, left:dict, right:Any) -> bool:
    """
    Returns: True if spec was complemented, False otherwise
    """
    if not isinstance(left, dict):
        raise Exception(f"Code bug: expected left argument as dict, got {left}")

    if "meta" in left:
        key=left["meta"].get("key")
        if key=="oif":
            nflow.dest.interface=right
            return True
        if key=="iif":
            nflow.src.interface=right
            return True
        return False

    if "payload" in left:
        payload=left["payload"]
        protocol=payload.get("protocol")
        field=payload.get("field")
        if protocol in ("udp", "tcp"):
            if field=="sport":
                nflow.src.add_protocol(protocol)
                if isinstance(right, dict) and "set" in right:
                    for item in right["set"]:
                        if isinstance(item, int):
                            nflow.src.add_port(int(item))
                        elif isinstance(item, dict) and "range" in item:
                            nflow.src.add_portrange(item["range"][0], item["range"][1])
                else:
                    nflow.src.add_port(int(right)) # pyright: ignore
                return True
            if field=="dport":
                nflow.dest.add_protocol(protocol)
                if isinstance(right, dict) and "set" in right:
                    for item in right["set"]:
                        if isinstance(item, int):
                            nflow.dest.add_port(int(item))
                        elif isinstance(item, dict) and "range" in item:
                            nflow.dest.add_portrange(item["range"][0], item["range"][1])
                else:
                    nflow.dest.add_port(int(right)) # pyright: ignore
                return True
            syslog.syslog(syslog.LOG_ERR, f"Unhandled field '{field}' in nftables rule left='{left}' right='{right}'")
        elif protocol=="ip":
            if field=="protocol":
                try:
                    try:
                        proto=protocols.protocol_ids[int(right)]
                    except ValueError:
                        protocols.protocol_names[right]
                        proto=right
                    nflow.src.add_protocol(proto)
                    nflow.dest.add_protocol(proto)
                    return True
                except Exception:
                    raise Exception(f"Unknown protocol ID '{right}', please update the protocols.py file")
            if field=="daddr":
                nflow.dest.add_address(_addr_to_object(right))
                return True
            if field=="saddr":
                nflow.src.add_address(_addr_to_object(right))
                return True
            syslog.syslog(syslog.LOG_ERR, f"Unhandled field '{field}' in nftables rule left='{left}' right='{right}'")
    return False

def _rule_match_netflow(rule_item:dict, nflow:netflow.NetFlow) -> tuple[bool, Policy|None]:
    """Tell if a rule matches the specified NetFlow
    """
    # create an empty NetFlow which will be complemented by each match element of the rule and used in the end
    # to make a response
    nflow_cmp=netflow.NetFlow(None, None)

    exprs=rule_item["expr"]
    policy=None
    for expr in exprs:
        if "match" in expr:
            mdata=expr["match"]
            if mdata["op"]=="==":
                match=_match_netflow(nflow_cmp, mdata["left"], mdata["right"])
                if _match_debug:
                    syslog.syslog(syslog.LOG_DEBUG, f"_match_netflow({nflow_cmp}, LEFT={mdata['left']}, RIGHT={mdata['right']}) ==> {match} ({nflow_cmp})")
                if not match:
                    return (False, None)
            else:
                return (False, None) # we only use "==" operators in this module
        elif "drop" in expr:
            if policy is None:
                policy=Policy.from_keyword("drop")
            else:
                raise Exception("Can't handle multiple statement in rule")
        elif "accept" in expr:
            if policy is None:
                policy=Policy.from_keyword("accept")
            else:
                raise Exception("Can't handle multiple statement in rule")
        elif "masquerade" in expr or "log" in expr:
            # extra information, not useful to match netflow
            pass
        else:
            return (False, None) # we only use "match" expressions in this module
    m=nflow==nflow_cmp
    if _match_debug:
        syslog.syslog(syslog.LOG_DEBUG, f"==> CMP '{nflow}' vs '{nflow_cmp}', return {m, policy if m else None}")
    return (m, policy if m else None)

def _find_chain_deny_log_rule(data:dict, table_name:str, chain_name:str, log_deny_spec:LogSpec|None, family:Family=Family.IPv4) -> int|None:
    """Get the handle number of the global rule to log deny accesses in a chain"""
    # ex. of item:
    # {
    #  "rule": {
    #    "family": "ip",
    #    "table": "filter",
    #    "chain": "FORWARD",
    #    "handle": 4,
    #    "expr": [
    #      {
    #        "log": {
    #          "prefix": "PADSI "
    #        }
    #      }
    #    ]
    #  }
    #}
    for item in data["nftables"]:
        data=item.get("rule")
        if data is not None:
            if data["table"]==table_name and data["family"]==family.value and \
               data["chain"]==chain_name:
                if len(data["expr"])==1:
                    sitem=data["expr"][0]
                    if "log" in sitem:
                        if log_deny_spec is not None and sitem["log"].get("prefix")==log_deny_spec.prefix or log_deny_spec is None:
                            return data["handle"]
    return None

def _find_rule(data:dict, table_name:str, chain_name:str, nflow:netflow.NetFlow) -> tuple[int|None, Policy|None]:
    """Find a rule in the specified data
    Return: the rule handle in the data if found and None if not found
    """
    if _match_debug:
        syslog.syslog(syslog.LOG_DEBUG, f"table:{table_name}, chain:{chain_name}, nflow:{nflow}")
    for item in data["nftables"]:
        rdata=item.get("rule")
        if rdata:
            if rdata["table"]==table_name and rdata["family"]=="ip" and \
               rdata["chain"]==chain_name:
                if _match_debug:
                    syslog.syslog(syslog.LOG_DEBUG, f"potential rule: {json.dumps(rdata, indent=4)}")
                (match, policy)=_rule_match_netflow(rdata, nflow)
                if match:
                    return (rdata["handle"], policy)
    return (None, None)

xt_conntrack_match={
    "type": "match",
    "name": "conntrack"
}
nft_conntrack_match={
    "op": "in",
    "left": {
        "ct": {
            "key": "state"
        }
    },
    "right": [
        "established",
        "related"
    ]
}

class FwTool:
    def __init__(self, nft_path:str, netns:str|None=None, log_denied_spec:LogSpec|None=None, objects_prefix:str|None=None):
        # The log_denied_spec is implemented as follows:
        # - if None: no deny logging is performed
        # - if not None:
        #    - a catch all deny rule is added at the end of each chain with a DENY policy
        #    - a log target is added for each DENY rule
        self._netns=netns
        self._bin_path=nft_path
        self._log_denied_spec=log_denied_spec
        self._objects_prefix=objects_prefix
        self._ensure_objects_exists()

    @property
    def log_denied(self) -> bool:
        """Tells if DENY requests are logged
        """
        return self._log_denied_spec is not None

    def _interface_exists(self, name:str) -> bool:
        """Tells if a network interface exists"""
        args=["ip", "link", "show", name]
        p=subprocess.run(self._args_with_nets(args), capture_output=True, text=True)
        if p.returncode!=0:
            if "does not exist" in p.stderr:
                return False
            syslog.syslog(syslog.LOG_WARNING, f"Could not get information about network interface {self._with_netns(name)}: {p.stderr} ({' '.join(args)})")
            return False
        return True

    def _ensure_interface_present(self, name:str, timeout_ms:int=60000):
        """Test if the specified interface is present, and wait for it a while if not
        """
        if not self._interface_exists(name):
            counter=0
            while counter<timeout_ms/100: # wait at most 1 minute
                time.sleep(0.1)
                if self._interface_exists(name):
                    return
                counter+=1

            # debug message with all the network interfaces present
            args=["ip", "link", "show"]
            p=subprocess.run(self._args_with_nets(args), capture_output=True, text=True)
            if p.returncode==0:
                msg=p.stdout
            else:
                msg=p.stderr
            raise Exception(f"Waited for it but network interface '{name}' does not exist (current interfaces: {msg})")

    def _with_netns(self, name:str|None=None):
        """Display helper"""
        if name is None:
            return f"in ns '{self._netns if self._netns else 'init'}'"
        return f"'{name}' in ns '{self._netns if self._netns else 'init'}'"

    def _args_with_nets(self, args:list[str]) -> list[str]:
        if self._netns is not None:
            return ["ip", "netns", "exec", self._netns]+args
        return args

    def _get_ruleset(self):
        # ex.:
        # {
        #   "nftables": [
        #     {
        #       "metainfo": {...}
        #     },
        #     {
        #       "table": {
        #         "family": "ip",
        #         "name": "filter",
        #         "handle": 2
        #       }
        #     },
        #     {
        #       "chain": {
        #         "family": "ip",
        #         "table": "filter",
        #         "name": "OUTPUT",
        #         "handle": 1,
        #         "type": "filter",
        #         "hook": "output",
        #         "prio": 0,
        #         "policy": "accept"
        #       }
        #     }
        #   ]
        # }
        args=[self._bin_path, "-j", "list", "ruleset"]
        p=subprocess.run(self._args_with_nets(args), capture_output=True, text=True)
        if p.returncode!=0:
            raise Exception(f"Could not list nftables's tables {self._with_netns()}: {p.stderr if p.stderr else p.stdout}")
        return json.loads(p.stdout)

    def _create_chain(self, table_name:str, chain_name:str, policy:Policy=Policy.ALLOW, family:Family=Family.IPv4):
        current=self._get_ruleset()
        (_handle, cpolicy)=_find_chain(current, table_name, chain_name, family)

        table_type=table_name
        if "_" in table_name:
            (_, table_type)=table_name.split("_")

        hook_type=chain_name.lower()
        if "_" in chain_name:
            (_, hook_type)=chain_name.split("_")
            hook_type=hook_type.lower()

        if table_name.endswith("_nat"):
            if chain_name.endswith("_POSTROUTING"):
                prio="srcnat"
            else:
                prio="dstnat"
        else:
            prio="filter"
        args=[self._bin_path, "add", "chain", family.value, table_name, chain_name,
              "{", "type", table_type, "hook", hook_type, "priority", prio, ";", "policy", policy.keyword, ";", "}"]
        p=subprocess.run(self._args_with_nets(args), capture_output=True, text=True)
        if p.returncode!=0:
            raise Exception(f"Could not add chain '{chain_name}' in '{table_name}' {self._with_netns()}: {p.stderr}")
        if _log_commands:
            syslog.syslog(syslog.LOG_DEBUG, f"creating chain {chain_name} ==> {policy} / {self._log_denied_spec}")

        if policy==cpolicy:
            return

        # adapt log rule
        h=_find_chain_deny_log_rule(self._get_ruleset(), table_name, chain_name,
            self._log_denied_spec, family=family)
        if policy==Policy.DENY:
            if self._log_denied_spec is not None and h is None:
                # add a catch all rule to log denied packets
                args=[self._bin_path, "add", "rule", family.value, table_name, chain_name]+self._log_denied_spec.get_nft_args()
                p=subprocess.run(self._args_with_nets(args), capture_output=True, text=True)
                if p.returncode!=0:
                    raise Exception(f"Could not set deny log as global chain rule: {p.stderr}")
        else:
            if h is not None:
                assert(self._log_denied_spec is not None)
                args=[self._bin_path, "delete", "rule", family.value, table_name, chain_name, "handle", str(h)]
                p=subprocess.run(self._args_with_nets(args), capture_output=True, text=True)
                if p.returncode!=0:
                    raise Exception(f"Could not remove deny log as global chain rule: {p.stderr}")

        current=self._get_ruleset()
        (_handle, cpolicy)=_find_chain(current, table_name, chain_name, family)
        if cpolicy!=policy:
            syslog.syslog(syslog.LOG_ERR, f"NFT create chain for NS {self._netns}, {table_name}.{chain_name} => expected {policy} got: {cpolicy}")

    def _ensure_objects_exists(self):
        """Make sure all the nftables' objects (tables and chains) we use are created
        """
        current=self._get_ruleset()
        for flowtype in FlowType:
            (table_name, chain_name)=_get_table_and_chain_from_flow(flowtype, self._objects_prefix)

            for family in (Family.IPv4, Family.IPv6):
                if _find_table(current, table_name, family=family) is None:
                    args=[self._bin_path, "add", "table", family.value, table_name]
                    p=subprocess.run(self._args_with_nets(args), capture_output=True, text=True)
                    if p.returncode!=0:
                        raise Exception(f"Could not add IP table '{table_name}' for family {family} {self._with_netns()}: {p.stderr}")

                (handle, _policy)=_find_chain(current, table_name, chain_name, family=family)
                if handle is None:
                    self._create_chain(table_name, chain_name, family=family)

    def set_default_policy(self, flowtype:FlowType, policy:Policy, family:Family=Family.IPv4):
        (table_name, chain_name)=_get_table_and_chain_from_flow(flowtype, self._objects_prefix)
        self._create_chain(table_name, chain_name, policy, family)

    def get_default_policy(self, flowtype:FlowType, family:Family=Family.IPv4) -> Policy|None:
        (table_name, chain_name)=_get_table_and_chain_from_flow(flowtype, self._objects_prefix)
        current=self._get_ruleset()
        (_handle, cpolicy)=_find_chain(current, table_name, chain_name, family)
        return cpolicy

    def flush(self, flowtype:FlowType, family:Family=Family.IPv4):
        (table_name, chain_name)=_get_table_and_chain_from_flow(flowtype, self._objects_prefix)
        args=[self._bin_path, "flush", "chain", family.value, table_name, chain_name]
        if _log_commands:
            syslog.syslog(syslog.LOG_DEBUG, f"flushing {family} chain {chain_name} ==> {' '.join(args)}")
        p=subprocess.run(self._args_with_nets(args), capture_output=True, text=True)
        if p.returncode!=0:
            raise Exception(f"Could not flush rules in table '{table_name}' and chain '{chain_name}' for family {family} {self._with_netns()}: {p.stderr}")

    def _find_related_connections_policy(self, flowtype:FlowType, family:Family=Family.IPv4) -> tuple[int|None, Policy|None]:
        (table_name, chain_name)=_get_table_and_chain_from_flow(flowtype, self._objects_prefix)
        current=self._get_ruleset()
        for item in current["nftables"]:
            if "rule" in item:
                rule=item["rule"]
                if rule.get("family")==family.value and rule.get("table")==table_name and rule.get("chain")==chain_name and \
                   rule.get("expr") is not None:
                    handle=None
                    policy=None
                    for expr in rule.get("expr"):
                        if expr.get("xt")==xt_conntrack_match or expr.get("match")==nft_conntrack_match:
                            handle=rule["handle"]
                        if "accept" in expr:
                            policy=Policy.ALLOW
                        if "deny" in expr:
                            policy=Policy.DENY
                    if handle is not None and policy is not None:
                        return (handle, policy)
        return (None, None)

    def set_related_connections_policy(self, flowtype:FlowType, policy:Policy, family:Family=Family.IPv4):
        (handle, cpolicy)=self._find_related_connections_policy(flowtype, family)
        if cpolicy==policy:
            return

        (table_name, chain_name)=_get_table_and_chain_from_flow(flowtype, self._objects_prefix)
        specargs=["ct", "state", "related,established"]
        if cpolicy is None:
            h=_find_chain_deny_log_rule(self._get_ruleset(), table_name, chain_name,
                self._log_denied_spec, family=family)
            if h is None:
                args=[self._bin_path, "add", "rule", family.value, table_name, chain_name]+specargs+[policy.keyword]
            else:
                args=[self._bin_path, "insert", "rule", family.value, table_name, chain_name, "handle", str(h)]+specargs+[policy.keyword]
            if _log_commands:
                syslog.syslog(syslog.LOG_DEBUG, f"==> {' '.join(args)}")
            p=subprocess.run(self._args_with_nets(args), capture_output=True, text=True)
            if p.returncode!=0:
                raise Exception(f"Could not set related connections policy in table '{table_name}', chain '{chain_name}' {self._with_netns()}: {p.stderr}")
        else:
            args=[self._bin_path, "insert", "rule", family.value, table_name, chain_name, "handle", str(handle)]+specargs+[policy.keyword]
            if _log_commands:
                syslog.syslog(syslog.LOG_DEBUG, f"==> {' '.join(args)}")
            p=subprocess.run(self._args_with_nets(args), capture_output=True, text=True)
            if p.returncode!=0:
                raise Exception(f"Could not insert related connections policy in table '{table_name}', chain '{chain_name}' {self._with_netns()}, @ {handle}: {p.stderr}")

            args=[self._bin_path, "delete", "rule", family.value, table_name, chain_name, "handle", str(handle)]
            if _log_commands:
                syslog.syslog(syslog.LOG_DEBUG, f"==> {' '.join(args)}")
            p=subprocess.run(self._args_with_nets(args), capture_output=True, text=True)
            if p.returncode!=0:
                raise Exception(f"Could not delete related connections policy in table '{table_name}', chain '{chain_name}' {self._with_netns()}, @ {handle}: {p.stderr}")

    def get_related_connections_policy(self, flowtype:FlowType, family:Family=Family.IPv4) -> Policy:
        (_handle, policy)=self._find_related_connections_policy(flowtype, family)
        return policy if policy is not None else Policy.DENY

    def flow_set_policy(self, flowtype:FlowType, nflow:netflow.NetFlow, policy:Policy, log_if_deny:bool=True, family:Family=Family.IPv4):
        (table_name, chain_name)=_get_table_and_chain_from_flow(flowtype, self._objects_prefix)
        current=self._get_ruleset()

        # if several protocols are specified, split into as many NetFlow objects
        flows=nflow.split_by_protocol()
        for (_protocol, pflow) in flows.items():
            (handle, cpolicy)=_find_rule(current, table_name, chain_name, pflow)
            if cpolicy==policy:
                return

            specargs=_netflow_to_nft_args(pflow)
            if cpolicy is None:
                h=_find_chain_deny_log_rule(current, table_name, chain_name,
                    self._log_denied_spec, family=family)
                if h is None:
                    args=[self._bin_path, "add", "rule", "ip", table_name, chain_name]+specargs
                else:
                    args=[self._bin_path, "insert", "rule", "ip", table_name, chain_name, "handle", str(h)]+specargs
                if self.log_denied and policy==Policy.DENY and log_if_deny:
                    assert(self._log_denied_spec is not None)
                    args+=self._log_denied_spec.get_nft_args()
                args.append(policy.keyword)

                if _log_commands:
                    syslog.syslog(syslog.LOG_DEBUG, f"==> {' '.join(args)}")
                p=subprocess.run(self._args_with_nets(args), capture_output=True, text=True)
                if p.returncode!=0:
                    raise Exception(f"Could not add rule in table '{table_name}', chain '{chain_name}', spec {pflow} {self._with_netns()}: {p.stderr}")
            else:
                args=[self._bin_path, "insert", "rule", "ip", table_name, chain_name, "handle", str(handle)]+specargs
                if self.log_denied and policy==Policy.DENY and log_if_deny:
                    assert(self._log_denied_spec is not None)
                    args+=self._log_denied_spec.get_nft_args()
                args.append(policy.keyword)
                if _log_commands:
                    syslog.syslog(syslog.LOG_DEBUG, f"==> {' '.join(args)}")
                p=subprocess.run(self._args_with_nets(args), capture_output=True, text=True)
                if p.returncode!=0:
                    raise Exception(f"Could not insert rule in table '{table_name}', chain '{chain_name}', @ {handle} spec {pflow} {self._with_netns()}: {p.stderr}")

                args=[self._bin_path, "delete", "rule", "ip", table_name, chain_name, "handle", str(handle)]
                if _log_commands:
                    syslog.syslog(syslog.LOG_DEBUG, f"==> {' '.join(args)}")
                p=subprocess.run(self._args_with_nets(args), capture_output=True, text=True)
                if p.returncode!=0:
                    raise Exception(f"Could not delete rule in table '{table_name}', chain '{chain_name}', @ {handle} {self._with_netns()}: {p.stderr}")

    def flow_get_policy(self, flowtype:FlowType, nflow:netflow.NetFlow) -> Policy:
        (table_name, chain_name)=_get_table_and_chain_from_flow(flowtype, self._objects_prefix)
        current=self._get_ruleset()
        flows=nflow.split_by_protocol()
        p=None
        for (_protocol, pflow) in flows.items():
            (_handle, cpolicy)=_find_rule(current, table_name, chain_name, pflow)
            if p is not None and p!=cpolicy:
                raise netflow.SubNewFlowDifferencesException()
            p=cpolicy
        if p is None:
            raise Exception(f"CODEBUG: could not identify Policy of netflow {nflow}")
        return p

    def flow_delete_policy(self, flowtype:FlowType, nflow:netflow.NetFlow):
        (table_name, chain_name)=_get_table_and_chain_from_flow(flowtype, self._objects_prefix)
        current=self._get_ruleset()

        flows=nflow.split_by_protocol()
        for (_protocol, pflow) in flows.items():
            (handle, _cpolicy)=_find_rule(current, table_name, chain_name, pflow)
            if handle is not None:
                args=[self._bin_path, "delete", "rule", "ip", table_name, chain_name, "handle", str(handle)]
                p=subprocess.run(self._args_with_nets(args), capture_output=True, text=True)
                if p.returncode!=0:
                    raise Exception(f"Could not delete rule in table '{table_name}', chain '{chain_name}', spec {pflow} {self._with_netns()}: {p.stderr}")

    def add_masquerade(self, out_iface:str|None=None, source_addr:ipaddress.IPv4Address|None=None, family:Family=Family.IPv4):
        (table_name, chain_name)=_get_table_and_chain_from_flow(FlowType.NAT_POSTROUTING, self._objects_prefix)
        h=_find_chain_deny_log_rule(self._get_ruleset(), table_name, chain_name,
            self._log_denied_spec, family=family)
        if out_iface:
            self._ensure_interface_present(out_iface, 2000)
            if h is None:
                args=[self._bin_path, "add", "rule", family.value, table_name, chain_name, "meta", "oif", out_iface, "masquerade"]
            else:
                args=[self._bin_path, "insert", "rule", family.value, table_name, chain_name, "handle", str(h), "meta", "oif", out_iface, "masquerade"]
        elif source_addr is None:
            raise Exception("Can't have both unspecified out_iface and source_addr")
        else:
            if h is None:
                args=[self._bin_path, "add", "rule", family.value, table_name, chain_name, "ip", "saddr", str(source_addr), "masquerade"]
            else:
                args=[self._bin_path, "insert", "rule", family.value, table_name, chain_name, "handle", str(h), "ip", "saddr", str(source_addr), "masquerade"]
        if _log_commands:
            syslog.syslog(syslog.LOG_DEBUG, f"==> {' '.join(args)}")
        p=subprocess.run(self._args_with_nets(args), capture_output=True, text=True)
        if p.returncode!=0:
            raise Exception(f"Could not add masquerading for out iface '{out_iface}' / source addr '{source_addr}' {self._with_netns()}: {p.stderr}")

    def del_masquerade(self, out_iface:str|None=None, source_addr:ipaddress.IPv4Address|None=None, family:Family=Family.IPv4):
        (table_name, chain_name)=_get_table_and_chain_from_flow(FlowType.NAT_POSTROUTING, self._objects_prefix)
        current=self._get_ruleset()
        args:list[str]|None=None
        if out_iface:
            self._ensure_interface_present(out_iface, 2000)
            nflow=netflow.NetFlow.from_repr(f"*>>#{out_iface}")
            (h, _cpolicy)=_find_rule(current, table_name, chain_name, nflow)
            if h is not None:
                args=[self._bin_path, "delete", "rule", family.value, table_name, chain_name, "handle", str(h)]
        elif source_addr is None:
            raise Exception("Can't have both unspecified out_iface and source_addr")
        else:
            nflow=netflow.NetFlow.from_repr(f"{source_addr}>>*")
            (h, _cpolicy)=_find_rule(current, table_name, chain_name, nflow)
            if h is not None:
                args=[self._bin_path, "delete", "rule", family.value, table_name, chain_name, "handle", str(h)]
        if args is not None:
            if _log_commands:
                syslog.syslog(syslog.LOG_DEBUG, f"==> {' '.join(args)}")
            p=subprocess.run(self._args_with_nets(args), capture_output=True, text=True)
            if p.returncode!=0:
                raise Exception(f"Could not del masquerading for out iface '{out_iface}' / source addr '{source_addr}' {self._with_netns()}: {p.stderr}")

    def del_stale_masquerade(self, out_iface_index:int, family:Family=Family.IPv4):
        (table_name, chain_name)=_get_table_and_chain_from_flow(FlowType.NAT_POSTROUTING, self._objects_prefix)
        current=self._get_ruleset()
        nflow=netflow.NetFlow.from_repr(f"*>>#{out_iface_index}")
        (h, _cpolicy)=_find_rule(current, table_name, chain_name, nflow)
        if h is not None:
            args=[self._bin_path, "delete", "rule", family.value, table_name, chain_name, "handle", str(h)]
            if _log_commands:
                syslog.syslog(syslog.LOG_DEBUG, f"==> {' '.join(args)}")
            p=subprocess.run(self._args_with_nets(args), capture_output=True, text=True)
            if p.returncode!=0:
                raise Exception(f"Could not del masquerading for out iface index '{out_iface_index}' {self._with_netns()}: {p.stderr}")

    def add_dnat(self, dest_addr:ipaddress.IPv4Address, in_iface:str|None, protocol_spec:str|None, port_spec:str|None, family:Family=Family.IPv4):
        """Add DNAT for traffic coming from the specified interface, protocol and port to the specified IP address
        Limitations:
            - in_iface and protocol_spec are mutually exclisive
            - if protocol_spec is None, port_spec can't be None
            - at least in_iface or protocol_spec must not be None
        """
        # checks
        if protocol_spec is None and in_iface is None:
            raise Exception("CODEBUG: in_iface and protocol_spec can't be None at the same time")
        if protocol_spec is None and port_spec is not None:
            raise Exception("CODEBUG: port_spec can't be speficied if protocol_spec is None")
        if in_iface is not None:
            self._ensure_interface_present(in_iface)

        # get deny rule handle
        (table_name, chain_name)=_get_table_and_chain_from_flow(FlowType.NAT_PREROUTING, self._objects_prefix)
        h=_find_chain_deny_log_rule(self._get_ruleset(), table_name, chain_name, self._log_denied_spec)

        # args. preparation
        multiargs:list[list[str]]=[]
        if in_iface is not None:
            multiargs=[["meta", "iif", in_iface, "dnat", "to", str(dest_addr)]]
        else:
            # analysis of inputs
            protocols=netflow.analyse_protocol_spec(protocol_spec)
            if protocols is None:
                raise Exception("CODEBUG: in_iface is None and protocol_spec did not yield any protocol")

            (ports, port_ranges)=netflow.analyse_port_spec(port_spec)
            if ports is None:
                ports=[]
            if port_ranges is None:
                port_ranges=[]
            ports=ports+port_ranges

            for proto in protocols:
                if len(ports)>0:
                    for item in ports:
                        multiargs.append([proto, "dport", str(item), "dnat", str(dest_addr)])
                else:
                    multiargs.append([proto, "dnat", str(dest_addr)])

        # actual execution
        for args in multiargs:
            if h is None:
                allargs=[self._bin_path, "add", "rule", family.value, table_name, chain_name]+args
            else:
                allargs=[self._bin_path, "insert", "rule", family.value, table_name, chain_name, "handle", str(h)]+args

            if _log_commands:
                syslog.syslog(syslog.LOG_DEBUG, f"==> {' '.join(allargs)}")
            p=subprocess.run(self._args_with_nets(allargs), capture_output=True, text=True)
            if p.returncode!=0:
                if in_iface is None:
                    raise Exception(f"Could not add DNAT '{protocol_spec}' and '{port_spec}' / desr addr '{dest_addr}' {self._with_netns()}: {p.stderr}")
                else:
                    raise Exception(f"Could not add DNAT from iface '{in_iface}' / desr addr '{dest_addr}' {self._with_netns()}: {p.stderr}")

        # allow forwarding
        self.flow_set_policy(FlowType.FILTER_FORWARD, netflow.NetFlow.from_repr(f"*>>{str(dest_addr)}"),
            Policy.ALLOW, family=family)
