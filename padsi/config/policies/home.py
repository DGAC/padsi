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


def initialize_home_policies(home_dir:str, uid:int, gid:int):
    """Initialize the HOME directory for global policies
    """
    # prevent p11-kit from injecting the OpenSC's PKCS#11 driver into the various NSS DB
    # refer ro man pkcs11.conf, and https://p11-glue.github.io/p11-glue/p11-kit/manual
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

    # pre-configure GPG if ever needed
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
