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

from .network import (FWRule, NetworkRessources, ResolvRule,
                      load_rules_from_data)


class Proxy:
    """Represent a proxy configuration"""
    def __init__(self, proxy:str, fw_rules:list[FWRule], resolv_rules:list[ResolvRule]|None, descr:str):
        # proxy should be "<target>[:<port number>]", no "http://" at the start, port 3128 will be used if not specified
        if proxy.startswith("http://") or proxy.startswith("https://"):
            raise Exception(f"Invalid proxy syntax '{proxy}' (no need to specify the HTTP or HTTPS protocol)")
        (host, *extra)=proxy.split(":")
        if not host:
            raise ValueError(f"Invalid proxy specification '{proxy}': empty server part")
        self._host=host

        if len(extra)==0:
            self._port=3128
        elif len(extra)==1:
            try:
                port=int(extra[0])
                if port<=0 or port>=65535:
                    raise Exception("invalid port part")
                self._port=port
            except Exception as e:
                raise ValueError(f"Invalid proxy specification '{proxy}': {str(e)}")
        else:
            raise ValueError(f"Invalid proxy specification '{proxy}': invalid format")
        self._proxy=proxy

        self._fw_rules:list[FWRule]=fw_rules
        self._resolv_rules:list[ResolvRule]|None=resolv_rules
        self._descr=descr

    def __str__(self) -> str:
        return f"{self._host}:{self._port}"

    @property
    def fw_rules(self) -> list[FWRule]|None:
        return self._fw_rules

    @property
    def resolv_rules(self) -> list[ResolvRule]|None:
        return self._resolv_rules

    @property
    def descr(self) -> str:
        return self._descr

    @property
    def host(self) -> str:
        return self._host

    @property
    def port(self) -> int:
        return self._port

    @classmethod
    def from_data(cls, data:dict|None, named_netres:dict[str,NetworkRessources]|None, rules_only:bool=False, config_dir:str|None=None) -> Proxy|None:
        if data is None:
            return None

        if not isinstance(data, dict):
            raise Exception(f"Invalid proxy definition {data}")

        (fw_rules, resolv_rules)=load_rules_from_data(data.get("out-rules", []), named_netres)

        proxy=data.get("proxy")
        if proxy is None:
            raise Exception("No 'proxy' section")
        return cls(proxy, fw_rules, resolv_rules, data.get("descr", "Unnamed"))
