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

import enum
from dataclasses import dataclass


class FlowType(str, enum.Enum):
    FILTER_INPUT = "filter.INPUT"
    FILTER_OUTPUT = "filter.OUTPUT"
    FILTER_FORWARD = "filter.FORWARD"
    NAT_POSTROUTING = "nat.POSTROUTING"
    NAT_PREROUTING = "nat.PREROUTING"

class Policy(str, enum.Enum):
    """Firewall rule policy"""
    ALLOW = "ALLOW"
    DENY = "DENY"

    @property
    def keyword(self):
        return "accept" if self.name=="ALLOW" else "drop"

    @property
    def KEYWORD(self):
        return "ACCEPT" if self.name=="ALLOW" else "DROP"

    @classmethod
    def from_keyword(cls, keyword:str) -> Policy:
        if keyword.lower()=="accept":
            return cls.ALLOW
        elif keyword.lower()=="drop":
            return cls.DENY
        raise Exception(f"Unknown keyword '{keyword}'")

class Family(str, enum.Enum):
    IPv4 = "ip"
    IPv6 = "ip6"
    INET = "inet"

@dataclass
class LogSpec:
    prefix: str
    group: int|None

    def __str__(self) -> str:
        return self.prefix if self.group is None else f"{self.prefix}@{self.group}"

    def get_nft_args(self) -> list[str]:
        if self.group is None:
            return ["log", "prefix", f'"{self.prefix} "']
        return ["log", "prefix", f'"{self.prefix}"', "group", str(self.group)]

    @classmethod
    def from_str(cls, data:str) -> LogSpec:
        if "@" in data:
            (*pre, group)=data.split("@")
            try:
                igroup=int(group)
            except Exception:
                raise Exception(f"Invalid NFLOG group '{group}'")
            return LogSpec("@".join(pre), igroup)
        else:
            return LogSpec(data, None)
