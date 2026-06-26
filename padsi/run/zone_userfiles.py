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


#
# Object to assemble the virtual HOME directory of a user for a zone
#

from __future__ import annotations

import os
import subprocess
import syslog

import padsi.config
import padsi.misc

_debug=False


def _compute_all_zones_xdg_directories(gconf:padsi.config.Configuration, uid:int, gid:int) -> dict[str,str]:
    """Compute the XDG directories (which may not exist yet)
    of all the zones for the user as a dictionary where:
    - key="<zone name>_<XDG dir>"
    - value=the actual directory in the filesystem
    """
    home_dir=gconf.get_zone_user_home_dir(uid)
    if not os.path.exists(home_dir):
        for dirname in (gconf.get_user_run_dir(uid), gconf.get_zone_user_home_dir(uid)):
            os.makedirs(dirname, exist_ok=True)
            os.chown(dirname, uid, gid)
            os.chmod(dirname, 0o700)

    user_xdg_subdirectories=padsi.misc.compute_user_xdg_subdirectories(uid)
    all_zones_dirs={}
    for name in gconf.get_zones_names():
        for xdg_dir in padsi.misc.xdg_dirs:
            key=f"{name}_{xdg_dir}"
            value=os.path.join(home_dir, name, user_xdg_subdirectories[xdg_dir])
            all_zones_dirs[key]=value
    return all_zones_dirs

class ZoneUserFiles:
    """Object which keeps a list of all the directories which are somehow mounted from a zone's definition
    to create the home directory of the user in that zone.

    Note: all the instances of a zone (ZoneApps and ZoneVM objects) are "associated" to
          the same ZoneUserFiles object
    """
    def __init__(self, zone_conf:padsi.config.Zone, uid:int, run_dir:str):
        self._zone_conf=zone_conf
        self._uid=uid
        self._syslogprefix=f"userfiles, zone {self._zone_conf.name}, uid {self._uid}"
        self._zone_home_dir:str=self.__class__._compute_assembled_home_dir(zone_conf, run_dir)

    @property
    def zone_conf(self) -> padsi.config.Zone:
        """Associated Zone configuration"""
        return self._zone_conf

    @property
    def syslog_prefix(self) -> str:
        return self._syslogprefix

    @property
    def zone_home_dir(self) -> str:
        """Directory (in the context of the "init" mount namespace) representing the $HOME of the user in the zone
        """
        return self._zone_home_dir

    @classmethod
    def compute_all_zones_dir(cls, run_dir:str) -> str:
        """Get the directory where all the user files are located, for a user and
        for all the zones. Will be like "/run/padsi/user/<uid>/userfiles"
        """
        return os.path.join(run_dir, "userfiles")

    @classmethod
    def _compute_assembled_home_dir(cls, zone_conf:padsi.config.Zone, run_dir:str) -> str:
        """Zone's home directory in the "host"
        """
        return os.path.join(cls.compute_all_zones_dir(run_dir), zone_conf.name)

    @classmethod
    def _generate_ssh_keypair(cls, zone_conf:padsi.config.Zone, run_dir:str, uid:int, gid:int,):
        """Generate an ED25519 SSH keypair not protected by any passphrase
        """
        top_dir=cls._compute_assembled_home_dir(zone_conf, run_dir)
        try:
            ssh_dir=os.path.join(top_dir, ".ssh")
            os.makedirs(ssh_dir, exist_ok=True)
            os.chmod(ssh_dir, 0o700)
            os.chown(ssh_dir, uid, gid)
            try:
                privkey_file=os.path.join(ssh_dir, "padsi-vm-key")
                if os.path.exists(privkey_file):
                    os.remove(privkey_file)
                proc=subprocess.run(["ssh-keygen", "-t", "ed25519", "-N", "", "-C", "PADSI generated", "-f", privkey_file], capture_output=True, text=True)
                if proc.returncode!=0:
                    raise Exception(proc.stderr.strip())
                os.chown(privkey_file, uid, gid)
                os.chown(privkey_file+".pub", uid, gid)
            except FileNotFoundError:
                raise Exception("ssk-keygen tool not found")
        except Exception as e:
            syslog.syslog(syslog.LOG_ERR, f"Could not generate SSH keypair: {str(e)}")

    @classmethod
    def get_ssh_pubkey_file(cls) -> str:
        """Get the SSH public key file name relative to the HOME directory
        Does not check if it exists or not
        """
        return os.path.join(".ssh", "padsi-vm-key.pub")

    @classmethod
    def compute_actual_mount_points(cls, gconf:padsi.config.Configuration, zone_conf:padsi.config.Zone,
                                    run_dir:str, uid:int, gid:int) -> list[padsi.config.MountPoint]:
        """Get the actual mount points in the specified context
        """
        user_xdg_subdirectories=padsi.misc.compute_user_xdg_subdirectories(uid)
        all_zones_dirs=_compute_all_zones_xdg_directories(gconf, uid, gid)
        host_home_dir=padsi.misc.get_user_home_dir(uid)
        for xdg_dir in padsi.misc.xdg_dirs:
            all_zones_dirs[xdg_dir]=os.path.join(host_home_dir, user_xdg_subdirectories[xdg_dir])

        top_dir=cls._compute_assembled_home_dir(zone_conf, run_dir)
        os.makedirs(top_dir, exist_ok=True)

        res:list[padsi.config.MountPoint]=[]

        # HOME dir of the zone as a mount point
        zone_home=gconf.get_zone_user_home_dir(uid, zone_conf.name)
        res.append(padsi.config.MountPoint(zone_home, top_dir , False))

        # one mount point per mount point defined in the zone's configuration
        all_zones_home=gconf.get_zone_user_home_dir(uid)
        for mp in zone_conf.mount_points:
            mountpoint=os.path.join(top_dir, padsi.misc.expand_variables_in_string(mp.mount_path, user_xdg_subdirectories))
            source_path=padsi.misc.expand_variables_in_string(mp.source_path, all_zones_dirs)
            if not os.path.isabs(source_path):
                source_path=os.path.join(host_home_dir, source_path)

            # create source path (and intermediary directories) if necessary
            if not os.path.exists(source_path):
                if source_path.startswith(host_home_dir):
                    padsi.misc.makedirs_with_owner(source_path, host_home_dir, uid, gid)
                elif source_path.startswith(all_zones_home):
                    padsi.misc.makedirs_with_owner(source_path, all_zones_home, uid, gid)
                else:
                    os.makedirs(source_path)

            #syslog.syslog(syslog.LOG_ERR, f"{source_path} will be mounted as {mountpoint} (RO: {mp.read_only})")
            res.append(padsi.config.MountPoint(source_path, mountpoint, mp.read_only))
        return res

    @classmethod
    def setup(cls, gconf:padsi.config.Configuration, zone_conf:padsi.config.Zone, uid:int, gid:int, run_dir:str, syslog_prefix:str) -> list[str]:
        """Actually mount all the directories required to make the zone's home directory of
        the user, and returns the list of mounted points (in the reverse order in which they were mouted)
        """
        if _debug:
            syslog.syslog(syslog.LOG_DEBUG, f"{syslog_prefix}: setup")

        # mounting ("assembling") directories
        mounted:list[padsi.config.MountPoint]=[]
        try:
            for mp in cls.compute_actual_mount_points(gconf, zone_conf, run_dir, uid, gid):
                if _debug:
                    mode="RO" if mp.read_only else "RW"
                    syslog.syslog(syslog.LOG_DEBUG, f"{syslog_prefix}: mounting {mp.source_path} to {mp.mount_path} ({mode})")
                if mp.mount():
                    mounted.append(mp)
            if _debug:
                syslog.syslog(syslog.LOG_DEBUG, f"{syslog_prefix}: setup done")
        except Exception as e:
            for mp in mounted[::-1]:
                if _debug:
                    syslog.syslog(syslog.LOG_DEBUG, f"{syslog_prefix}: unmounting {mp.mount_path} from {mp.source_path}")
                try:
                    mp.umount()
                except Exception as se:
                    syslog.syslog(syslog.LOG_WARNING, f"{syslog_prefix}: {str(se)}")
            msg=f"{syslog_prefix}: setup failed: {str(e)}"
            syslog.syslog(syslog.LOG_ERR, msg)
            raise Exception(msg)

        # # (re) initialize any policy located in the HOME directory
        factory=padsi.config.ProgramPoliciesFactory()
        top_dir=cls._compute_assembled_home_dir(zone_conf, run_dir)
        padsi.config.initialize_home_policies(top_dir, uid, gid)
        for progname in factory.supported_programs:
            if _debug:
                syslog.syslog(syslog.LOG_DEBUG, f"{syslog_prefix}: (re) initializing (HOME) policies for '{progname}'")
            policies=factory.get_program_policies(progname, uid, gid)
            if policies is not None:
                try:
                    policies.initialize_user_policies(home_dir=top_dir)
                except Exception as e:
                    syslog.syslog(syslog.LOG_ERR, f"{syslog_prefix}: failed to initialize (HOME) policies for {progname}: {str(e)}")

        # create SSH keys if the zone can host VMs
        if zone_conf.has_virtual_machines:
            cls._generate_ssh_keypair(zone_conf, run_dir, uid, gid)

        return [mp.mount_path for mp in mounted[::-1]]

    @classmethod
    def cleanup(cls, gconf:padsi.config.Configuration, zone_conf:padsi.config.Zone, uid:int, gid:int, run_dir:str, syslog_prefix:str):
        revmp=cls.compute_actual_mount_points(gconf, zone_conf, run_dir, uid, gid)[::-1]
        for mp in revmp:
            try:
                if _debug:
                    syslog.syslog(syslog.LOG_DEBUG, f"{syslog_prefix}: unmounting {mp.mount_path} from {mp.source_path}")
                mp.umount()
            except Exception as e:
                syslog.syslog(syslog.LOG_WARNING, f"{syslog_prefix}: {str(e)}")
