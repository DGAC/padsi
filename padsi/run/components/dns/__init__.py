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

from __future__ import annotations

import grp
import ipaddress
import json
import os
import pwd
import re
import shutil
import syslog
import tempfile
from itertools import groupby

import firewall
import nsbubble
import padsi.config

from .. import Component

_debug = False


class DNSServer(Component):
    """DNS server"""

    def __init__(
        self,
        resolv_rules: list[padsi.config.ResolvRule] | None,
        resolvers: list[padsi.config.network.DNSEndpoint] | None,
        log_denied_spec: firewall.LogSpec | None = None,
        log_only: bool = False,
        denied_fallback_ip: str | None = None,
        has_web_proxy: bool = False,
        dns_block_list: str|None=None
    ):
        """Create a DNS server component.
        - resolv_rules: set of rules to resolve
        - resolvers: static list of resolvers to use. If None, not external DNS resolution will be performed, and
          if [], then the default resolvers (/etc/resolv.conf) will be used
        - log_only: log requests, don't actually block deny requests
        """
        self._resolv_rules_conf: list[padsi.config.ResolvRule] = resolv_rules if resolv_rules is not None else []
        self._resolv_rules_extra: dict[str, list[padsi.config.ResolvRule]] = {}  # key=a specific context, value= list of extra rules for that usage
        self._tmpdir: tempfile.TemporaryDirectory | None = None
        self._resolv_rules_file: str | None = None
        self._dns_fw_config_file: str | None = None

        self._resolvers = resolvers
        self._resolv_conf_file: str | None = None
        self._dns_blocklist=dns_block_list

        self._log_only = log_only
        self._denied_fallback_ip = denied_fallback_ip
        self._log_denied_spec = log_denied_spec
        self._sandbox_dir_obj: tempfile.TemporaryDirectory | None = None  # stores all files needed to run the programs in the nsbubble
        self._sandbox_dir_name: str | None = None

        self._has_web_proxy=has_web_proxy

        self._pid1: int | None = None  # DNS server PID
        self._pid2: int | None = None  # FW manager's PID

    def _recreate_resolv_rules(self):
        """Update the self._resolv_rules_file file's contents"""
        if self._tmpdir is None:
            self._tmpdir = tempfile.TemporaryDirectory()
        if self._resolv_rules_file is None:
            self._resolv_rules_file = os.path.join(self._tmpdir.name, "resolv-rules.json")

        extra_rules = []
        for _, erules in self._resolv_rules_extra.items():
            if erules is not None and len(erules) > 0:
                extra_rules += erules
        if _debug:
            syslog.syslog(syslog.LOG_DEBUG, f"Recreating resolv rules {self._resolv_rules_file} {self._resolv_rules_conf + extra_rules}")

        data: list = []
        for rule in self._resolv_rules_conf + extra_rules:
            data += rule.format_for_component()
        with open(self._resolv_rules_file, "wt") as fd:
            fd.write(json.dumps(data))

    def _recreate_resolv_conf(self):
        """Update the self._resolv_conf_file file's contents"""
        if self._tmpdir is None:
            self._tmpdir = tempfile.TemporaryDirectory()
        if self._resolv_conf_file is None:
            self._resolv_conf_file = os.path.join(self._tmpdir.name, "resolv-conf.json")
        if _debug:
            syslog.syslog(syslog.LOG_DEBUG, f"Recreating resolv config {self._resolv_conf_file}")

        data: list = []
        if self._resolvers is not None:
            if len(self._resolvers)==0:
                syslog.syslog(syslog.LOG_ERR, "Using system's DNS servers is not yet implemented")
            else:
                for dns_endpoint in self._resolvers:
                    data.append(
                        {
                            "ip": str(dns_endpoint.ip_address),
                            "port": dns_endpoint.port,
                            "proto": dns_endpoint.protocol,
                        }
                    )
        with open(self._resolv_conf_file, "wt") as fd:
            fd.write(json.dumps(data))

    def _create_dns_fw_config_file(self):
        """Create the configuration file for the padsi-dns-fw program
        """
        if self._tmpdir is None:
            self._tmpdir = tempfile.TemporaryDirectory()
        if self._dns_fw_config_file is None:
            self._dns_fw_config_file = os.path.join(self._tmpdir.name, "dns-fw-conf.json")
        if _debug:
            syslog.syslog(syslog.LOG_DEBUG, f"Creating DNS FW config {self._dns_fw_config_file}")

        data={
            "log-denied-spec": str(self._log_denied_spec),
            "output-allow": []
        }
        if self._has_web_proxy:
            for rule in self._resolv_rules_conf:
                if rule.action=="allow":
                    for item in rule.endpoint.domain_zones:
                        if item not in ("wpad.", "proxy."):
                            data["output-allow"].append(item)
        with open(self._dns_fw_config_file, "wt") as fd:
            fd.write(json.dumps(data))

    def add_extra_rules(self, context: str, rules: list[padsi.config.ResolvRule]):
        self._resolv_rules_extra[context] = rules
        self._recreate_resolv_rules()

    def get_mountpoints(self) -> dict:
        script_dir = os.path.dirname(__file__)

        # copy the resources to a tmp directory
        if self._sandbox_dir_name is None:
            self._sandbox_dir_obj = tempfile.TemporaryDirectory()
            self._sandbox_dir_name = self._sandbox_dir_obj.name

            # unbound's config
            conf_dir = f"{self._sandbox_dir_name}/unbound"
            os.makedirs(conf_dir)
            shutil.copyfile(
                f"{script_dir}/unbound/unbound.conf", f"{conf_dir}/unbound.conf"
            )
            shutil.copytree(f"{script_dir}/unbound/conf.d", f"{conf_dir}/conf.d")

            # scripts
            bin_dir = f"{self._sandbox_dir_name}/bin"
            os.makedirs(bin_dir)
            for file in ("padsi-dns-server", "padsi-dns-fw"):
                shutil.copy2(f"{script_dir}/{file}", f"{bin_dir}")
            shutil.copyfile(
                f"{script_dir}/unbound/unbound-module.py",
                f"{bin_dir}/unbound-module.py",
            )
            shutil.copytree(f"{script_dir}/../../../../firewall", f"{bin_dir}/firewall")
        else:
            conf_dir = f"{self._sandbox_dir_name}/unbound"
            bin_dir = f"{self._sandbox_dir_name}/bin"

        self._recreate_resolv_rules()
        self._recreate_resolv_conf()
        self._create_dns_fw_config_file()

        mounts={
            conf_dir: {
                "mount-point": "/etc/unbound",
                "read-only": False,
                "monitored": False,
            },
            "/usr/sbin/unbound": {  # unbound binary to avoid apparmor restrictions
                "mount-point": "/tmp/unbound",
                "read-only": True,
                "monitored": False,
            },
            f"{bin_dir}": {
                "mount-point": "/padsi-dns-bin",
                "read-only": True,
                "monitored": False,
            },
            self._resolv_rules_file: {
                "mount-point": "/etc/resolv-rules.json",
                "read-only": True,
                "monitored": True,
            },
            self._resolv_conf_file: {
                "mount-point": "/etc/resolv-conf.json",
                "read-only": True,
                "monitored": True,
            },
            self._dns_fw_config_file: {
                "mount-point": "/etc/dns-fw-conf.json",
                "read-only": True,
                "monitored": False,
            }
        }
        if self._dns_blocklist is not None:
            mounts[self._dns_blocklist]={
                "mount-point": "/etc/unbound/conf.d/blocklist.conf",
                "read-only": True,
                "monitored": False,
            }

        return mounts

    def get_required_user_entry(self) -> str:
        uid = os.geteuid()
        user = pwd.getpwuid(uid)
        unbound = pwd.getpwnam("unbound")
        return f"{unbound.pw_name}:x:{user.pw_uid}:{user.pw_gid}:{user.pw_gecos}:/home/{user.pw_name}:{unbound.pw_shell}"

    def get_required_group_entry(self) -> str | None:
        gid = os.getegid()
        group = grp.getgrgid(gid)
        unbound = grp.getgrnam("unbound")
        return f"{unbound.gr_name}:x:{group.gr_gid}:{','.join(unbound.gr_mem)}"

    @property
    def capabilities(self) -> list[str]:
        return ["net_admin", "net_bind_service"]

    def start(self, api: nsbubble.BubbleAPI):
        """Actually start the required processes in a bubble using the api object"""
        env = {"LOG_ONLY": "yes"} if self._log_only else {}
        if self._denied_fallback_ip is not None:
            env["DENIED_FALLBACK_IP"] = self._denied_fallback_ip
        if self._pid1 is None:
            self._pid1 = api.start_process(
                ["/padsi-dns-bin/padsi-dns-server"],
                extra_env=env,
                ignore_status=False,
                capabilities="net_bind_service",
                restart=True
            )
        if self._pid2 is None:
            args=["/padsi-dns-bin/padsi-dns-fw", "/etc/dns-fw-conf.json"]
            self._pid2 = api.start_process(args, extra_env=env, ignore_status=False, capabilities="net_admin", restart=True)

    def stop(self, api: nsbubble.BubbleAPI):
        """Stop processes"""
        if self._pid1 is not None:
            api.stop_process(self._pid1)
            self._pid1 = None
        if self._pid2 is not None:
            api.stop_process(self._pid2)
            self._pid2 = None

        if self._sandbox_dir_obj is not None:
            self._sandbox_dir_obj.cleanup()
            self._sandbox_dir_obj = None
        self._sandbox_dir_name = None

    def serialize(self) -> dict:
        return {
            "class": self.__class__.__name__,
            "data": {
                "log-only": self._log_only,
                "log-deny-spec": str(self._log_denied_spec),
                "dir": self._sandbox_dir_name,
                "pid1": self._pid1,
                "pid2": self._pid2,
            },
        }

    @classmethod
    def deserialize(cls, data: dict) -> DNSServer:
        ldata = data.get("data", {})
        obj = cls([], None, firewall.LogSpec.from_str(ldata["log-deny-spec"]), ldata["log-only"])
        obj._sandbox_dir_name = ldata["dir"]
        obj._pid1 = ldata["pid1"]
        obj._pid2 = ldata["pid2"]
        return obj


def _is_ipv4_element(addr: str) -> bool:
    try:
        ipaddress.IPv4Address(addr)
        return True
    except Exception:
        try:
            ipaddress.IPv4Network(addr)
            return True
        except Exception:
            return False


def _is_domain_name(domain: str, allow_wildcards: bool = False) -> bool:
    """Return True if @domain represents a valid domain name (and not an IP address)"""
    if _is_ipv4_element(domain):
        return False
    if allow_wildcards:
        # make sure we don't have more than 2 consecutive "*" chars
        groups = groupby(domain)
        for k, c in [(k, sum(1 for _ in g)) for k, g in groups]:
            if k == "*" and c > 2:
                return False
        d = domain.replace("**", "aaa.bbb").replace("*", "ccc")
        return _is_domain_name(d, allow_wildcards=False)
    else:
        domain_regex = r"(([\da-zA-Z])([_\w-]{,62})\.){,127}(([\da-zA-Z])[_\w-]{,61})?([\da-zA-Z]\.((xn\-\-[a-zA-Z\d]+)|([a-zA-Z\d]{2,})))\.?$"
        valid_domain_name_regex = re.compile(domain_regex, re.IGNORECASE)
        return bool(re.match(valid_domain_name_regex, domain))


def validate_resolv_rules(rules: list):
    if not isinstance(rules, list):
        raise Exception(f"Resolv rules must be a dict, not a {type(rules)}")
    try:
        for rule in rules:
            action = rule.get("action")
            query = rule.get("query")
            reply = rule.get("reply")
            spec = rule.get("spec")

            if action not in ("allow", "deny"):
                raise Exception(f"Rule's action must be 'allow' or 'deny', not '{action}'")

            if not isinstance(query, str):
                raise Exception(f"Rule's query must be a str, not {type(query)}")
            if not _is_domain_name(query, allow_wildcards=True):
                raise Exception(f"Invalid query '{query}'")

            if reply is not None:
                for entry in reply:
                    try:
                        (typ, *resp) = entry.split("/")
                        if typ == "A":
                            if len(resp) != 2:
                                raise Exception("expected <response-validity>/<response as IPv4>")
                            ipaddress.IPv4Address(resp[1])
                        else:
                            raise Exception(f"unknown reply type '{typ}'")
                    except Exception as e:
                        syslog.syslog(syslog.LOG_ERR, f"Rule reply '{reply}' is invalid: {str(e)}")

            if spec is not None:
                try:
                    firewall.Endpoint.from_repr(f"* ^ {spec}")
                except Exception:
                    raise Exception(f"Rule spec '{spec}' is invalid")
    except Exception as e:
        raise Exception(f"Invalid resolv. rules: {str(e)}")
