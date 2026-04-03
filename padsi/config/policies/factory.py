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

from functools import cache

from .policies import ProgramPolicies


@cache
class ProgramPoliciesFactory:
    @property
    def supported_browsers(self) -> list[str]:
        """List all the Web browser for which policies can be defined
        """
        return ["firefox", "chromium"]

    @property
    def default_browser(self) -> str:
        return "firefox"

    @property
    def supported_programs(self) -> list[str]:
        """List all programs for which policies can be defined
        """
        return self.supported_browsers

    def get_program_policies(self, progname:str, uid:int|None=None, gid:int|None=None) -> ProgramPolicies|None:
        """Instantiate a ProgramPolicies which can handle policies of
        the specified program
        """
        match progname:
            case "chromium":
                from .chromium import ChromiumPolicies
                return ChromiumPolicies(uid, gid)
            case "firefox":
                from .firefox import FirefoxPolicies
                return FirefoxPolicies(uid, gid)
            case _:
                return None
