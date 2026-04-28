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


#
# This is the PADSI Web proxy and/or Web redirection component
#

from __future__ import annotations

import ipaddress
import json
import os
import syslog
import tempfile

import nsbubble
from firewall import Endpoint
from padsi.config import FWRule, FWRuleChain, Proxy, ResolvRule

from .. import Component
from .ca import RedirectCA

_debug=False

class WebInfra(Component):
    """Web server which can act as a Web proxy (and directly connect to the requested Web server or forward requests to some others Web proxies), and
    a Web "catch all" server which is able to reply as any web server which is not allowed in a specified zone:
    - creates a CA to generate any certificate
    - generates certificates on the fly
    - present a "blocked site" notice to the user in place of the requested site
    - if the user wants it, proposes to open the requested site in the same browser in another zone
    """
    def __init__(self, listening_ip: ipaddress.IPv4Interface|None, proxies: list[Proxy]|None, web_redir:bool, direct_access_rules:list[FWRule|ResolvRule]|None):
        self._listening_ip:ipaddress.IPv4Interface|None=listening_ip
        self._proxies=proxies if proxies is not None else []
        self._web_redir=web_redir
        self._direct_access_rules=direct_access_rules
        self._sandbox_dir_obj=tempfile.TemporaryDirectory() # stores all files needed to tun the programs in the nsbubble
        self._sandbox_dir_name:str=self._sandbox_dir_obj.name

        self._ca:RedirectCA|None=None
        if web_redir:
            self._ca=RedirectCA()

        self._pid:int|None=None # PID of the catch all web server

    def get_mountpoints(self) -> dict:
        """Get the mount points required by the component
        Cf. nsbubble's documentation for the formalism
        """
        mounts={
            self._sandbox_dir_name: {
                "mount-point": "/etc/web-infra",
                "read-only": True,
                "monitored": False
            }
        }

        if self._listening_ip is None:
            syslog.syslog(syslog.LOG_ERR, "CODEBUG: self._listening_ip should not be None")
        else:
            fname=os.path.join(self._sandbox_dir_name, "resolv.conf")
            with open(fname, "wt") as fd:
                fd.write(f"nameserver    {str(self._listening_ip.ip)}\n")
            mounts[fname]={
                "mount-point": "/etc/resolv.conf",
                "read-only": True,
                "monitored": False
            }

        return mounts

    def get_root_cert(self) -> str|None:
        """Get the root CA certificats, PEM encoded
        """
        if self._ca is None:
            return None
        with open(self._ca.ca_cert_file, "rt") as fd:
            return fd.read()

    @property
    def network_rules(self) -> tuple[list[FWRule], list[ResolvRule]]:
        """Web proxy specific rules to be added for the proxy to work properly, such as access to the proxy itself"""
        fw_rules=[]
        resolv_rules = []
        for proxy in self._proxies:
            fw_rules = [
                FWRule(
                    "allow",
                    "Web proxy access",
                    Endpoint.from_repr(f"{proxy.host} ^ tcp ^ {proxy.port}"),
                    FWRuleChain.OUTPUT,
                )
            ]

        if len(self._proxies)>0:
            # allow access to the proxy's own DNS server and web-infra (redirection purposes)
            if self._direct_access_rules is not None:
                if self._listening_ip is None:
                    syslog.syslog(syslog.LOG_ERR, "CODEBUG: self._listening_ip should not be None")
                else:
                    fw_rules.append(FWRule(
                        "allow",
                        "Dedicated DNS",
                        Endpoint.from_repr(f"{str(self._listening_ip.ip)} ^ udp ^ 53"),
                        FWRuleChain.OUTPUT,
                    ))
                    fw_rules.append(FWRule(
                        "allow",
                        "Web proxy with web redirection",
                        Endpoint.from_repr(f"{str(self._listening_ip.ip)} ^ tcp ^ 443,8443"),
                        FWRuleChain.OUTPUT,
                    ))

        return (fw_rules, resolv_rules)

    def _generate_config_file(self) -> str:
        """Returns the basename of the generated config file
        """
        config_file=os.path.join(self._sandbox_dir_name, "config.json")

        # Web proxy
        conf_data={}
        if len(self._proxies)>0:
            targets:list=[]
            for proxy in self._proxies:
                rules:list[dict]=[]
                if proxy.fw_rules is not None:
                    for rule in proxy.fw_rules:
                        rules.append({
                            "action": rule.action,
                            "endpoint": str(rule.endpoint)
                        })
                if proxy.resolv_rules is not None:
                    for rule in proxy.resolv_rules:
                        rules.append({
                            "action": rule.action,
                            "endpoint": str(rule.endpoint)
                        })
                targets.append({
                    "remote_proxy": f"{proxy.host}:{proxy.port}",
                    "rules": rules
                })

            # maybe add a "null" target to proxy traffic which is directly allowed
            if self._direct_access_rules is not None and len(self._direct_access_rules)>0:
                rules=[{
                    "action": rule.action,
                    "endpoint": str(rule.endpoint)
                } for rule in self._direct_access_rules]
                rules.append({
                    "action": "allow",
                    "endpoint": "*.vm."
                })
                targets.append({
                    "remote_proxy": None,
                    "rules": rules
                })

            conf_data["web_proxy"]={
                "listening_port": 3128,
                "listening_ip": str(self._listening_ip.ip) if self._listening_ip is not None else None,
                "targets": targets
            }
        else:
            conf_data["web_proxy"]=None

        # Web redirection
        if self._web_redir:
            conf_data["web_redirector"]={
                "http_ports": [ 80, 8080 ],
                "https_ports": [ 443, 8443 ],
                "listening_ip": str(self._listening_ip.ip) if self._listening_ip is not None else None,
            }
        else:
            conf_data["web_redirector"]=None

        with open(config_file, "wt") as fd:
            json.dump(conf_data, fd)

        return "config.json"

    def start(self, api:nsbubble.BubbleAPI):
        if self._pid is None:
            config_file=self._generate_config_file()
            if self._sandbox_dir_name is None:
                raise Exception("CODEBUG: self._sandbox_dir_name should not be None")

            script_dir=os.path.dirname(__file__)
            args=[os.path.join(script_dir, "web-infra"), os.path.join("/etc/web-infra", config_file)]
            password=None
            if self._ca is not None:
                p12_file=os.path.join(self._sandbox_dir_name, "ca.p12")
                password=self._ca.generate_ca_pkcs12(p12_file)
                args+=[os.path.join("/etc/web-infra", "ca.p12")]
                self._ca.delete_private_key()
                if _debug:
                    syslog.syslog(syslog.LOG_DEBUG, f"self._ca cert: {self.get_root_cert()}")

            if _debug:
                syslog.syslog(syslog.LOG_DEBUG, "Starting web infra component")
            self._pid=api.start_process(args, ignore_status=False, capabilities="net_bind_service",
                child_stdin=password+"\n" if password is not None else None, restart=True,
                child_stdout_file="/tmp/web-infra.stdout", child_stderr_file="/tmp/web-infra.stderr")
            if _debug:
                syslog.syslog(syslog.LOG_DEBUG, f"Started web infra component, PID: {self._pid}")

    def stop(self, api:nsbubble.BubbleAPI):
        if self._pid is not None:
            api.stop_process(self._pid)
            self._pid=None

        if self._sandbox_dir_obj is not None:
            self._sandbox_dir_obj.cleanup()
            self._sandbox_dir_obj=None

    def serialize(self) -> dict:
        return {
            "class": self.__class__.__name__,
            "data": {
                "dir": self._sandbox_dir_name,
                "pid": self._pid
            }
        }

    @classmethod
    def deserialize(cls, data:dict) -> WebInfra:
        ldata = data.get("data")
        if ldata is None:
            raise Exception("CODEBUG: no 'data' found in deserialized data")
        obj=cls(None, [], False, direct_access_rules=None)
        obj._sandbox_dir_name=ldata["dir"]
        obj._pid=ldata["pid"]
        return obj
