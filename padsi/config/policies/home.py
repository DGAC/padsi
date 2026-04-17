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
import syslog


def initialize_home_policies(home_dir:str, uid:int, gid:int):
    """Initialize the HOME directory for global policies, and performs some cleanups
    for previous sessions's leftovers
    """
    # prevent p11-kit from injecting the OpenSC's PKCS#11 driver into the various NSS DB
    # refer ro man pkcs11.conf, and https://p11-glue.github.io/p11-glue/p11-kit/manual
    try:
        parts=[".config", "pkcs11", "modules"]
        mod_path=os.path.join(home_dir, *parts)
        if not os.path.exists(mod_path):
            os.makedirs(mod_path)
            path=home_dir
            for part in parts:
                path=os.path.join(path, part)
                os.chown(path, uid, gid)

        conf_file=os.path.join(mod_path, "opensc-pkcs11.module")
        with open(conf_file, "wt") as fd:
            # extends /usr/share/p11-kit/modules/opensc-pkcs11.module
            fd.write("""
module: opensc-pkcs11
disable-in: firefox nss
""")
    except Exception as e:
        syslog.syslog(syslog.LOG_ERR, f"Failed to prevent p11-kit from injecting the OpenSC's PKCS#11 driver: {str(e)}")

    # pre-configure GPG if ever needed
    try:
        gpg_path=os.path.join(home_dir, ".gnupg")
        if not os.path.exists(gpg_path):
            os.makedirs(gpg_path)
            os.chown(gpg_path, uid, gid)
            os.chmod(gpg_path, 0o700)

        gpg_conf=os.path.join(gpg_path, "scdaemon.conf")
        if not os.path.exists(gpg_conf):
            with open(gpg_conf, "wt") as fd:
                fd.write("""
disable-ccid
pcsc-shared
""")
            os.chown(gpg_conf, uid, gid)
    except Exception as e:
        syslog.syslog(syslog.LOG_ERR, f"Failed to preconfigure GPG: {str(e)}")

    # clean any leftovers regarding SSH and VMs
    ssh_dir=os.path.join(home_dir, ".ssh")
    if os.path.exists(ssh_dir):
        os.chown(ssh_dir, uid, gid)
        os.chmod(ssh_dir, 0o700)

        # ssh keys
        try:
            fpath=os.path.join(ssh_dir, "known_hosts")
            with open(fpath, "rt") as fd:
                kept:list[str]=[]
                for line in fd.readlines():
                    (host, key)=line.split(maxsplit=1)
                    if not host.endswith(".vm"):
                        kept.append(line)
                fd.close()
                with open(fpath, "wt") as fd:
                    for line in kept:
                        fd.write(line)
            os.chown(fpath, uid, gid)
            os.chmod(fpath, 0o600)
        except FileNotFoundError:
            pass
        except Exception as e:
            syslog.syslog(syslog.LOG_ERR, f"Failed to clean up SSH keys leftovers: {str(e)}")

        # ssh config
        try:
            fpath=os.path.join(ssh_dir, "config")
            with open(fpath, "rt") as fd:
                kept=[]
                do_copy = True
                for line in fd.readlines():
                    if line.startswith("Host "):
                        (_, *targets) = line.split()
                        vmline=False
                        for t in targets:
                            if t.endswith(".vm"):
                                vmline=True
                                break
                        if vmline:
                            do_copy = False
                        else:
                            do_copy = True
                            kept.append(line)
                    elif do_copy:
                        kept.append(line)
                fd.close()
                with open(fpath, "wt") as fd:
                    for line in kept:
                        fd.write(line)
            os.chown(fpath, uid, gid)
            os.chmod(fpath, 0o600)
        except FileNotFoundError:
            pass
        except Exception as e:
            syslog.syslog(syslog.LOG_ERR, f"Failed to clean up SSH config leftovers: {str(e)}")
