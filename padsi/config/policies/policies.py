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

from abc import ABC, abstractmethod

import nsbubble

class ProgramPolicies(ABC):
    """Generic class representing policies which can be applied for a given program like Firefox or Chrome.

    It is at the same time a place to store configuration information and
    to deploy that configuration in the context of a zone.

    Dependning on the targeted program, Policies' configuration may involve writing to file in the user's HOME
    directory and/or in the system's diretories (which in PADSI's context will be writable by the user as they
    are mounted from a user specific directory when BubbleWrap is started).

    Note about some almost always present arguments:
    - The system_dir specifies the path in the host to the place where directories refined by the
      get_writable_directories() actually are
    - The home_dir specified the path in the host to the $HOME in the zone
    """
    @abstractmethod
    def initialize_user_policies(self, home_dir:str):
      """Allow user's policies to be (re)initialized.
      """
      pass

    @abstractmethod
    def get_directories(self) -> list[str]:
        """Get the list of directories in which some configuration files may be
        writen to implement the policy

        Note: Each directory listed here must be an absolute directory
        """
        return []

    @abstractmethod
    def add_trusted_ca(self, mountpoint_set:nsbubble.MountPointSet, home_dir:str, nickname:str, ca_cert:str):
        """Add a trusted CA certificate (PEM encoded string)
        Refer to class documentation for system_dir and home_dir.
        """
        pass

    @abstractmethod
    def add_pkcs11_driver(self, mountpoint_set:nsbubble.MountPointSet, home_dir:str, driver_name:str, driver_path:str):
        """Ensure the program is set up to use the PKCS#11 specified driver
        Refer to class doculentation for system_dir and home_dir.
        """
        pass

    @abstractmethod
    def get_open_url_arguments(self, url) -> list[str]:
        """Get the arguments required to open an URL in the browser
        """
        pass
