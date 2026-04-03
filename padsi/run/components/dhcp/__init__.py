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
# This is the PADSI local DHCP server component
#

from __future__ import annotations

import ipaddress
import json
import os
import tempfile

import nsbubble

from .. import Component


class DHCPServer(Component):
    """DNS server in a bubble"""

    def __init__(self, interfaces:list[str], server_ip:ipaddress.IPv4Interface, pool_start:ipaddress.IPv4Address, pool_end:ipaddress.IPv4Address,
                 resolver_ips:list[ipaddress.IPv4Address], router_ips:list[ipaddress.IPv4Address], mtu:int|None):
        """Create a DNS server component.
        - resolver_ips: list of resolvers' IPs which the DHCP will offer to clients in the "domain-name-servers" option
        - router_ips: list of default routers' IPs which the DHCP will offer to clients in the "routers" option
        """
        self._interfaces=interfaces
        self._subnet=server_ip.network
        self._server_ip=server_ip
        if pool_start not in self._subnet:
            raise Exception(f"Invalid pool start address {pool_start}: not in subnet")
        if pool_end not in self._subnet:
            raise Exception(f"Invalid pool end address {pool_end}: not in subnet")
        if pool_start>pool_end:
            raise Exception("Invalid pool extrmity addresses: wrong order")
        self._pool_start=pool_start
        self._pool_end=pool_end
        self._resolver_ips=resolver_ips
        self._router_ips=router_ips
        self._sandbox_dir_obj:tempfile.TemporaryDirectory|None=None # stores all files needed to tun the programs in the nsbubble
        self._sandbox_dir_name:str|None=None

        self._config_file=None
        self._net_mtu=mtu
        self._tapvm_configured=False
        self._pid:int|None=None # PID of the DHCP server

    def get_mountpoints(self) -> dict:
        """Get the mount points required by the component
        Cf. nsbubble's documentation for the formalism
        """
        script_dir=os.path.dirname(__file__)

        # create the configuration to a tmp directory
        if self._sandbox_dir_name is None:
            self._sandbox_dir_obj=tempfile.TemporaryDirectory()
            self._sandbox_dir_name=self._sandbox_dir_obj.name

            # kea's config
            src_file=f"{script_dir}/kea/kea-dhcp.conf.templ"
            with open(src_file, "r") as fd:
                conf=json.load(fd)
            conf["Dhcp4"]["interfaces-config"]["interfaces"]=self._interfaces
            conf["Dhcp4"]["option-data"][0]["data"]=",".join([str(ip) for ip in self._resolver_ips])
            subnet=conf["Dhcp4"]["subnet4"][0]
            subnet["subnet"]=str(self._subnet)
            subnet["pools"][0]["pool"]=f"{str(self._pool_start)} - {str(self._pool_end)}"
            subnet["option-data"][0]["data"]=",".join([str(ip) for ip in self._router_ips])

            self._config_file=f"{self._sandbox_dir_name}/kea-dhcp.conf"
            with open(self._config_file, "w") as fd:
                fd.write(json.dumps(conf))

            run_dir=f"{self._sandbox_dir_name}/kea"
            os.makedirs(run_dir)
        else:
            run_dir=f"{self._sandbox_dir_name}/kea"

        return {
            self._config_file: {
                "mount-point": "/etc/kea-dhcp.conf",
                "read-only": True,
                "monitored": False
            },
            run_dir: {
                "mount-point": "/run/kea",
                "read-only": False,
                "monitored": False
            },
            f"{os.path.dirname(__file__)}/tapvm-setup.sh": {
                "mount-point": "/tmp/tapvm-setup.sh",
                "read-only": True,
                "monitored": False
            },
            f"{os.path.dirname(__file__)}/dirs-setup.sh": {
                "mount-point": "/tmp/dirs-setup.sh",
                "read-only": True,
                "monitored": False
            },
            "/usr/sbin/kea-dhcp4": { # kea binary to avoid apparmor restrictions
                "mount-point": "/tmp/kea-dhcp4",
                "read-only": True,
                "monitored": False
            }
        }

    @property
    def capabilities(self) -> list[str]:
        return ["net_admin", "net_bind_service", "net_raw"]

    def start(self, api:nsbubble.BubbleAPI):
        if not self._tapvm_configured:
            for iface in self._interfaces:
                args=["/tmp/tapvm-setup.sh", iface, str(self._server_ip)]
                if self._net_mtu is not None:
                    args.append(str(self._net_mtu))
                pid=api.start_process(args, ignore_status=False, capabilities="net_admin")
                st=api.get_process_exit_status(pid, wait=15)
                if st!=0:
                    raise Exception(f"Failed to configure network interface '{iface}' with IP address '{str(self._server_ip)}' (/tmp/tapvm-setup.sh exits status is {st})")
            self._tapvm_configured=True
        if self._pid is None:
            pid=api.start_process(["/tmp/dirs-setup.sh"], ignore_status=False)
            st=api.get_process_exit_status(pid, wait=15)
            if st!=0:
                raise Exception(f"Failed to configure Kea directories (/tmp/dirs-setup.sh exits status is {st})")
            self._pid=api.start_process(["/tmp/kea-dhcp4", "-c", "/etc/kea-dhcp.conf"], ignore_status=False,
                capabilities="net_bind_service,net_raw", restart=True)

    def stop(self, api:nsbubble.BubbleAPI):
        if self._pid is not None:
            api.stop_process(self._pid)
            self._pid=None

        if self._sandbox_dir_obj is not None:
            self._sandbox_dir_obj.cleanup()
            self._sandbox_dir_obj=None
        self._sandbox_dir_name=None

    def serialize(self) -> dict:
        return {
            "class": self.__class__.__name__,
            "data": {
                "interfaces": self._interfaces,
                "server-ip": str(self._server_ip),
                "pool-start": str(self._pool_start),
                "pool-end": str(self._pool_end),
                "resolver-ips": [str(i) for i in self._resolver_ips],
                "router-ips": [str(i) for i in self._router_ips],
                "dir": self._sandbox_dir_name,
                "config-file": self._config_file,
                "tapvm-configured": self._tapvm_configured,
                "pid": self._pid,
                "mtu": self._net_mtu
            }
        }

    @classmethod
    def deserialize(cls, data:dict) -> DHCPServer:
        ldata=data.get("data")
        if ldata is None:
            raise Exception("CODEBUG: no 'data' found in deserialized data")
        obj=cls(ldata["interfaces"], ipaddress.IPv4Interface(ldata["server-ip"]), ipaddress.IPv4Address(ldata["pool-start"]),
                ipaddress.IPv4Address(ldata["pool-end"]), [ipaddress.IPv4Address(r) for r in ldata["resolver-ips"]],
                [ipaddress.IPv4Address(r) for r in ldata["router-ips"]], ldata["mtu"])
        obj._sandbox_dir_name=ldata["dir"]
        obj._config_file=ldata["config-file"]
        obj._tapvm_configured=ldata["tapvm-configured"]
        obj._pid=ldata["pid"]
        return obj
