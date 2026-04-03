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
import subprocess
import syslog
import tempfile
import time

gc=0

class NSSDB:
    """Class to manipulate NSS DB files"""
    def __init__(self, path:str):
        self._path=path
        self._dbarg=f"sql:{path}" # newer syntax, we don't support legacy DBs

    def _run_certutil(self, args:list[str], context:str|None) -> str|None:
        counter=0
        global gc
        gc+=1
        while True:
            rargs=["certutil", "-d", self._dbarg]+args
            try:
                proc=subprocess.run(rargs, capture_output=True, text=True)
            except FileNotFoundError:
                msg="Could not find the 'certutil' program, no NSS database can be used by PADSI"
                syslog.syslog(syslog.LOG_WARNING, msg)
                raise Exception(msg)

            if proc.returncode==255:
                # may be a temporary error while the DB is being initialized
                time.sleep(0.3)
                counter+=1
                if counter==5:
                    raise Exception(f"{context if context is not None else ''}(DB in {self._dbarg}): always getting returncode 255 ({proc.stderr})")
            elif proc.returncode!=0:
                if context is None:
                    return None
                raise Exception(f"{context if context is not None else ''}(DB in {self._dbarg}): {proc.stderr}")
            else:
                return (proc.stdout)

    def chown(self, uid:int, gid:int):
        """Change the ownership of the files making up the NSS DB
        """
        proc=subprocess.run(["chown", "-R", f"{uid}:{gid}", self._path], capture_output=True, text=True)
        if proc.returncode!=0:
            raise Exception(f"Failed to change ownership of NSS DB to {uid}:{gid}: {proc.stderr}")

    @property
    def exists(self) -> bool:
        """Tells if the NSS database actually exists and can be used without
        generating any error
        """
        if not os.path.exists(self._path):
            return False

        for fname in ("pkcs11.txt", "cert9.db", "key4.db"):
            if not os.path.exists(os.path.join(self._path, fname)):
                return False

        try:
            proc=subprocess.run(["certutil", "-d", self._dbarg, "-L"], capture_output=True, text=True)
        except FileNotFoundError:
            syslog.syslog(syslog.LOG_WARNING, "Could not find the 'certutil' program, no NSS database can be used by PADSI")
            return False

        if proc.returncode!=0:
            return False
        return True

    def clear_ca_certificates(self):
        """Remove all CA certificates
        """
        data=self._run_certutil(["-L"], "Failed to list certificates in NSSDB")
        if data is not None:
            for line in data.splitlines():
                try:
                    (*_, attrs)=line.split()
                    attrs=attrs.split(",")
                    if len(attrs)==3:
                        for attr in attrs:
                            for letter in attr:
                                if letter not in "cCT":
                                    raise Exception()

                        # get name
                        (_, rname)=line[::-1].split(maxsplit=1)
                        nickname=rname[::-1]
                        if nickname:
                            self._run_certutil(["-D", "-n", nickname], f"Failed to remove CA certificate '{nickname}'")
                except Exception:
                    # line does not contain a certificate's info
                    pass

    def add_ca_certificate(self, ca_cert:str, cert_nickname:str):
        """Add (replace if necessary) a CA certificate
        """

        try:
            proc=subprocess.run(["certutil", "-d", self._dbarg, "-L", "-n", cert_nickname], capture_output=True, text=True)
            if proc.returncode==0:
                # certificate already present, delete it
                proc=subprocess.run(["certutil", "-d", self._dbarg, "-D", "-n", cert_nickname], capture_output=True, text=True)
        except FileNotFoundError as e:
            syslog.syslog(syslog.LOG_ERR, "Could not find the 'certutil' program")
            raise e

        # import certificate as trusted CA for the Web
        with tempfile.NamedTemporaryFile("wt") as tmp:
            tmp.write(ca_cert)
            tmp.flush()
            self._run_certutil(["-A", "-t", "C,,", "-n", cert_nickname, "-i", tmp.name], "Failed to import CA certificate")

    def add_pkcs11_driver(self, driver_name:str, driver_path:str):
        """Declare a PKCS#11 driver
        """
        try:
            args=["modutil", "-force", "-dbdir", self._dbarg, "-add", driver_name, "-libfile", driver_path]
            proc=subprocess.run(args, capture_output=True, text=True)
            if proc.returncode!=0:
                # keeping getting weird error "Probable cause: "Failure to load dynamic library""
                # so check if module has been imported or not before syslog the error
                args=["modutil", "-dbdir", self._dbarg, "-list", driver_name]
                vproc=subprocess.run(args, capture_output=True, text=True)
                if vproc.returncode!=0:
                    raise Exception(f"Failed to load PKCS#11 driver '{driver_path}' (DB in {self._dbarg}): {proc.stderr if proc.stderr else proc.stdout}")
        except FileNotFoundError as e:
            syslog.syslog(syslog.LOG_ERR, "Could not find the 'modutil' program")
            raise e

    def ca_certificate_del(self, cert_nickname:str):
        """Delete if necessary a CA certificate
        """
        self._run_certutil(["-D", "-n", cert_nickname], None)
