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

from dataclasses import dataclass

from nsbubble import MountPointSet
from .nssdb import NSSDB
from .policies import ProgramPolicies

@dataclass
class PoliciesFile:
    read_path:str|None # path in the host, may be None if file does not yet exist
    write_path:str # path in the host
    bubble_path:str # path in the bubble

    def load(self) -> dict:
        try:
            with open(self.write_path, "rt") as fd:
                return json.load(fd)
        except FileNotFoundError:
            if self.read_path is not None:
                try:
                    with open(self.read_path, "rt") as fd:
                        return json.load(fd)
                except FileNotFoundError:
                    return {}
            return {}

    def write(self, data:dict):
        os.makedirs(os.path.dirname(self.write_path), exist_ok=True)
        with open(self.write_path, "wt") as fd:
            json.dump(data, fd, indent=4)

class FirefoxPolicies(ProgramPolicies):
    def __init__(self, uid:int|None=None, gid:int|None=None):
        self._uid=None
        self._gid=None
        if uid is not None and gid is None or \
            uid is None and gid is not None:
            raise Exception("Both uid and gid must be None or not None at the same time")
        if uid is not None and uid!=os.geteuid():
            self._uid=uid
            self._gid=gid
        self._pol_dirs=["/etc/firefox"] # Debian specific, way vary on other distributions, may not yet exist

    def get_directories(self) -> list[str]:
        # Debian specific: Firefox ESR's config file will be in /etc/firefox-esr, refer to https://wiki.debian.org/Firefox
        if os.path.exists("/etc/firefox-esr"):
            return self._pol_dirs+["/etc/firefox-esr"]
        return self._pol_dirs

    def _get_policies_files(self, mp_set:MountPointSet) -> list[PoliciesFile]:
        """Get each of Firefox's policies files (depending on the versions of Firefox installed
        in the system) as a dictionary where keys are paths in the host and associated values are paths in the the sandbox,
        the first one for the READ usage and the other for the WRITE usage (both may be equal)
        """
        res=[]
        for pol_dir in self._pol_dirs:
            pol_file=os.path.join(pol_dir, "policies", "policies.json")
            r_pol_file=mp_set.file_source_path(pol_file, False)
            w_pol_file=mp_set.file_source_path(pol_file, True)
            if w_pol_file is None:
                raise Exception(f"CODEBUG: MountPointSet.file_source_path({pol_file}, True) returned None")
            res.append(PoliciesFile(r_pol_file, w_pol_file, pol_file))
        return res

    def initialize_user_policies(self, home_dir:str):
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

    def add_trusted_ca(self, mountpoint_set:MountPointSet, home_dir:str, nickname:str, ca_cert:str):
        # refer to https://mozilla.github.io/policy-templates/#certificates
        # Note: when Firefox loads that policy, it will import the CA certificate in the user's
        #       profile's NSS database
        for pol_file in self._get_policies_files(mountpoint_set):
            h_certs_dir=os.path.dirname(os.path.dirname(pol_file.write_path))
            if h_certs_dir=="/":
                raise Exception("CODEBUG: certs_dir is '/'")
            h_certs_dir=os.path.join(h_certs_dir, "padsi-certs")
            os.makedirs(h_certs_dir, exist_ok=True)

            b_certs_dir=os.path.dirname(os.path.dirname(pol_file.bubble_path))
            b_certs_dir=os.path.join(b_certs_dir, "padsi-certs")

            # create file for the CA certificate
            hash=hashlib.sha256(ca_cert.encode()).hexdigest()
            cert_file=os.path.join(h_certs_dir, f"{hash}.crt")
            if not os.path.exists(cert_file):
                with open(cert_file, "wt") as fd:
                    fd.write(ca_cert)

            # update the policies file
            pol_data=pol_file.load()
            if "policies" not in pol_data:
                pol_data["policies"]={}
            if "Certificates" not in pol_data["policies"]:
                pol_data["policies"]["Certificates"]={}
            if "Install" not in pol_data["policies"]["Certificates"]:
                pol_data["policies"]["Certificates"]["Install"]=[]
            zone_cert_file=os.path.join(b_certs_dir, f"{hash}.crt")
            pol_data["policies"]["Certificates"]["Install"].append(zone_cert_file)
            pol_file.write(pol_data)

    def add_pkcs11_driver(self, mountpoint_set:MountPointSet, home_dir:str, driver_name:str, driver_path:str):
        # refer to hhttps://mozilla.github.io/policy-templates/#securitydevices
        for pol_file in self._get_policies_files(mountpoint_set):
            pol_data=pol_file.load()
            if "policies" not in pol_data:
                pol_data["policies"]={}
            if "SecurityDevices" not in pol_data["policies"]:
                pol_data["policies"]["SecurityDevices"]={}
            if "Add" not in pol_data["policies"]["SecurityDevices"]:
                pol_data["policies"]["SecurityDevices"]["Add"]={}
            pol_data["policies"]["SecurityDevices"]["Add"][driver_name]=driver_path
            pol_file.write(pol_data)

    def get_open_url_arguments(self, url) -> list[str]:
        return ["firefox", "--no-remote", url]
