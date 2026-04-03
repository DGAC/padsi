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

import hashlib
import json
import os

from .nssdb import NSSDB
from .policies import ProgramPolicies


class FirefoxPolicies(ProgramPolicies):
    policies_dir="/etc/firefox"

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
        return [FirefoxPolicies.policies_dir]

    def _get_real_policies_file(self, root_path:str):
        real_dir=os.path.join(root_path, FirefoxPolicies.policies_dir[1:])
        real_pol_dir=os.path.join(real_dir, "policies")
        os.makedirs(real_pol_dir, exist_ok=True)
        return os.path.join(real_pol_dir, "policies.json")

    def _load_current_policies(self, real_policies_file:str) -> dict:
        try:
            with open(real_policies_file, "rt") as fd:
                return json.load(fd)
        except FileNotFoundError:
            pass
        return {}

    def _write_new_policies(self, real_policies_file:str, data:dict):
        with open(real_policies_file, "wt") as fd:
            json.dump(data, fd, indent=4)

    def initialize_policies(self, system_dir:str|None=None, home_dir:str|None=None):
        if system_dir:
            # empty the policies if it exists
            pol_file=self._get_real_policies_file(system_dir)
            try:
                os.remove(pol_file)
            except FileNotFoundError:
                pass

        if home_dir:
            # remove any trusted certificate from any local NSS database
            for (root, dirs, files) in os.walk(home_dir):
                dbpath=os.path.join(home_dir, root)
                nssdb=NSSDB(dbpath)
                if nssdb.exists:
                    # root contains an NSS database
                    try:
                        nssdb.clear_ca_certificates()
                        if self._uid is not None and self._gid is not None:
                            nssdb.chown(self._uid, self._gid)
                    except Exception as e:
                        raise Exception(f"Failed to clean NSS database in '{dbpath}': {str(e)}")

    def add_trusted_ca(self, system_dir:str, home_dir:str, nickname:str, ca_cert:str):
        # refer to https://mozilla.github.io/policy-templates/#certificates
        # Note: when Firefox loads that policy, it will import the CA certificate in the user's
        #       profile's NSS database
        pol_file=self._get_real_policies_file(system_dir)

        real_dir=os.path.join(system_dir, FirefoxPolicies.policies_dir[1:])
        real_certs_dir=os.path.join(real_dir, "padsi-certs")
        os.makedirs(real_certs_dir, exist_ok=True)

        # create file for the CA certificate
        hash=hashlib.sha256(ca_cert.encode()).hexdigest()
        cert_file=os.path.join(real_certs_dir, f"{hash}.crt")
        if not os.path.exists(cert_file):
            with open(cert_file, "wt") as fd:
                fd.write(ca_cert)

        # update the policies file
        pol_data=self._load_current_policies(pol_file)
        if "policies" not in pol_data:
            pol_data["policies"]={}
        if "Certificates" not in pol_data["policies"]:
            pol_data["policies"]["Certificates"]={}
        pol_data["policies"]["Certificates"]["ImportEnterpriseRoots"]=True
        if "Install" not in pol_data["policies"]["Certificates"]:
            pol_data["policies"]["Certificates"]["Install"]=[]
        zone_cert_file=os.path.join(FirefoxPolicies.policies_dir, "padsi-certs", f"{hash}.crt")
        pol_data["policies"]["Certificates"]["Install"].append(zone_cert_file)
        self._write_new_policies(pol_file, pol_data)

    def add_pkcs11_driver(self, system_dir:str, home_dir:str, driver_name:str, driver_path:str):
        # refer to hhttps://mozilla.github.io/policy-templates/#securitydevices
        pol_file=self._get_real_policies_file(system_dir)

        # update the policies file
        pol_data=self._load_current_policies(pol_file)
        if "policies" not in pol_data:
            pol_data["policies"]={}
        if "SecurityDevices" not in pol_data["policies"]:
            pol_data["policies"]["SecurityDevices"]={}
        if "Add" not in pol_data["policies"]["SecurityDevices"]:
            pol_data["policies"]["SecurityDevices"]["Add"]={}
        pol_data["policies"]["SecurityDevices"]["Add"][driver_name]=driver_path
        self._write_new_policies(pol_file, pol_data)

    def get_open_url_arguments(self, url) -> list[str]:
        return ["firefox", "--no-remote", url]
