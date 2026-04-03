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

import os

from .nssdb import NSSDB
from .policies import ProgramPolicies


# Refer to:
# - https://www.chromium.org/administrators/linux-quick-start/
# - https://chromium.googlesource.com/chromium/src.git/+/master/docs/linux/cert_management.md
# - https://wiki.archlinux.org/title/Network_Security_Services
class ChromiumPolicies(ProgramPolicies):
    def __init__(self, uid:int|None=None, gid:int|None=None):
        self._uid=None
        self._gid=None
        if uid is not None and gid is None or \
            uid is None and gid is not None:
            raise Exception("Both uid and gid must be None or not None at the same time")
        if uid is not None and uid!=os.geteuid():
            self._uid=uid
            self._gid=gid

    def get_writable_directories(self) -> list[str]:
        return ["/etc/chromium", "/etc/chromium.d"]

    def _get_nssdb_path(self, home_dir:str) -> str:
        path0=os.path.join(home_dir, ".pki")
        path=os.path.join(path0, "nssdb")
        os.makedirs(path, exist_ok=True, mode=0o700)
        if self._uid is not None and self._gid is not None:
            os.chown(path0, self._uid, self._gid)
            os.chown(path, self._uid, self._gid)
        return path

    def initialize_policies(self, system_dir:str|None=None, home_dir:str|None=None):
        if home_dir:
            # remove any trusted certificate from the NSS database
            nssdb=NSSDB(self._get_nssdb_path(home_dir))
            if nssdb.exists:
                nssdb.clear_ca_certificates()
                if self._uid is not None and self._gid is not None:
                    nssdb.chown(self._uid, self._gid)

    def add_trusted_ca(self, system_dir:str, home_dir:str, nickname:str, ca_cert:str):
        # chromium uses the $HOME/.pki/nssdb NSS file for its certificates
        nssdb=NSSDB(self._get_nssdb_path(home_dir))
        nssdb.add_ca_certificate(ca_cert, nickname)
        if self._uid is not None and self._gid is not None:
            nssdb.chown(self._uid, self._gid)

    def add_pkcs11_driver(self, system_dir:str, home_dir:str, driver_name:str, driver_path:str):
        nssdb=NSSDB(self._get_nssdb_path(home_dir))
        nssdb.add_pkcs11_driver(driver_name, driver_path)
        if self._uid is not None and self._gid is not None:
            nssdb.chown(self._uid, self._gid)

    def get_open_url_arguments(self, url) -> list[str]:
        return ["chromium", "--enable-features=UseOzonePlatform", "--ozone-platform=wayland", url]
