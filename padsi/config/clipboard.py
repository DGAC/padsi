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


class Policy(str, enum.Enum):
    """Clipboard rule policy"""
    ALLOW = "ALLOW"
    DENY = "DENY"

    @classmethod
    def from_keyword(cls, keyword:str) -> Policy:
        if keyword.lower()=="allow":
            return cls.ALLOW
        elif keyword.lower()=="deny":
            return cls.DENY
        raise Exception(f"Unknown keyword '{keyword}'")

class ClipboardRule:
    def __init__(self, descr:str|None, action:str, rule:str):
        """Represents a single copy/paste rule
        """
        if action not in ("allow", "deny"):
            raise Exception(f"Invalid action '{action}'")
        self._action=action
        try:
            (copy_zones, paste_zones)=rule.split(">")
            copy_zones=copy_zones.split(",")
            copy_zones=[v.strip() for v in copy_zones]
            paste_zones=paste_zones.split(",")
            paste_zones=[v.strip() for v in paste_zones]
            if len(paste_zones)==0:
                raise Exception
            self._policy=Policy.from_keyword(action)
            self._descr=descr
            self._copy_zones=copy_zones
            self._paste_zones=paste_zones
        except Exception:
            raise Exception(f"Invalid clipboard rule '{rule}'")

    def get_policy(self, copy_zone:str, paste_zone:str) -> Policy|None:
        """If the rule applies for a copy/paste, returns the associated policy, otherwise
        returns None
        """
        if ("*" in self._copy_zones or copy_zone in self._copy_zones) and \
            ("*" in self._paste_zones or paste_zone in self._paste_zones):
                return self._policy
        return None

    def check_zones_exist(self, existing_zones:list[str]):
        """Check that all references zones exist, raise an exception if not
        """
        for name in self._copy_zones:
            if name!="*" and name not in existing_zones:
                raise Exception(f"Unknown referenced copy zone '{name}'")
        for name in self._paste_zones:
            if name!="*" and name not in existing_zones:
                raise Exception(f"Unknown referenced paste zone '{name}'")

    @classmethod
    def from_data(cls, data:dict) -> ClipboardRule:
        action=data.get("action")
        if action is None:
            raise Exception("Invalid null clipboard action")
        rule=data.get("rule")
        if rule is None:
            raise Exception("Invalid null clipboard rule")
        return cls(data.get("descr"), action, rule)
