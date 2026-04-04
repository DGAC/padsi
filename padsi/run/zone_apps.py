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
# PADSI object to represent instanciated ("running") Zones
#

from __future__ import annotations

import ipaddress
import os
import pwd
import re
import shutil
import subprocess
import syslog
import time

import psutil

import nsbubble
import padsi.config
import padsi.misc

from .components import fuse
from .dbus import ZoneDBusRouter
from .vm.vmfiles import VMFiles
from .zone_foundations import ZoneFoundations
from .zone_infra import ZoneInfra
from .zone_userfiles import ZoneUserFiles


def _get_PATH_environ(uid:int) -> str|None:
    p1=psutil.Process(1) # system's init process
    for p2 in p1.children():
        if p2.uids().real==uid and p2.name()=="systemd":
            for p3 in p2.children():
                if p3.uids().real==uid and p3.name()=="gnome-shell":
                    return p3.environ().get("PATH")
    return None

class ZoneApps(ZoneFoundations):
    """Instance of a zone in which user's applications are run
    """
    def __init__(
        self,
        global_conf: padsi.config.Configuration,
        zone_conf: padsi.config.Zone,
        uid: int,
        run_dir: str,
        logs_dir: str,
        zone_infra:ZoneInfra|None,
        zuf:ZoneUserFiles,
        ip_address:ipaddress.IPv4Interface|None,
        zone_service_socket:str|None=None,
        extra_root_cert:str|None=None,
        extra_env:dict[str,str]|None=None
    ):
        super().__init__("APPS", global_conf, zone_conf, None, uid, run_dir, logs_dir)
        self._z_infra=zone_infra
        self._zuf=zuf
        self._infra_dns_ip=zone_infra.bridge_ip.ip if zone_infra is not None else None
        self._zone_service_socket:str|None=zone_service_socket
        self._ip_address=ip_address
        self._extra_root_cert=extra_root_cert

        path=_get_PATH_environ(uid)
        self._extra_env={} if extra_env is None else extra_env.copy()
        if path is not None:
            self._extra_env["PATH"]=path
        self._extra_env["PYTHONPATH"]="/usr/share/padsi"

        self._dbus_env_in_host:dict[str,str]|None=None
        self._dbus_socket_path_in_host:str|None=None
        self._dbus_router:ZoneDBusRouter|None=None

        self._with_x11:bool|None=None # changed when forced

        self._prepare_components()

    def _prepare_components(self):
        with_fuse=self.zone_conf.get_option(padsi.config.ZoneOptionType.FUSE).enabled
        if with_fuse:
            user_def=pwd.getpwuid(self.uid)
            bhome_dir=os.path.join("/home", f"{user_def.pw_name}")
            comp=fuse.Fuse(os.path.join(self.run_dir, "padsi-fuse.sock"), self._logs_dir)
            comp.declare_bubble_mounted_dir(bhome_dir, self.home_dir)
            comp.declare_bubble_mounted_dir("/bubble/run", self.run_dir)
            self.add_component(comp)

    @property
    def ip_address(self) -> ipaddress.IPv4Interface|None:
        """IP address as an ipaddress.IPv4Interface of the zone from
        the point of view of the zone's network
        """
        return self._ip_address

    @property
    def home_dir(self) -> str:
        """Home directory for the zone (in the context of the "init" mount namespace)
        """
        return self._zuf.zone_home_dir

    @property
    def env_variables(self) -> dict[str,str]:
        if self.api is None:
            raise Exception("env_variables property: zone has not yet been started")
        return {} if self.api.environment is None else self.api.environment

    @property
    def dbus_router(self) -> ZoneDBusRouter|None:
        return self._dbus_router

    @property
    def with_x11(self) -> bool:
        if self._with_x11 is not None:
            return self._with_x11
        return self.zone_conf.get_option(padsi.config.ZoneOptionType.X11).enabled

    @with_x11.setter
    def with_x11(self, forced_with_x11:bool):
        self._with_x11=forced_with_x11

    def _start_zone_dbus(self):
        """Start the DBus session in the bubble"""
        syslog.syslog(syslog.LOG_DEBUG, f"{self.syslog_prefix}: starting DBUS")
        if self.api is None:
            raise Exception("DBus start: zone has not yet been started")

        # (re)create XDG desktop directories
        self.api.start_process(["xdg-user-dirs-update", "--force"], ignore_status=True)

        # grab dbus-launch's stdout
        (tmpdir, tmpdir_bubble)=self.api.create_shared_tempory_directory()
        stdout_fname=f"{tmpdir.name}/stdout.fifo"
        os.mkfifo(stdout_fname, mode=0o777)
        ofd=os.open(stdout_fname, os.O_RDONLY | os.O_NONBLOCK)

        # remove the DISPLAY and WAYLAND_DISPLAY env. variables, dbus-launch does not like them
        env=self.api.environment
        if "DISPLAY" in env:
            del env["DISPLAY"]
        if "WAYLAND_DISPLAY" in env:
            del env["WAYLAND_DISPLAY"]

        args=["dbus-launch", "--sh-syntax", "--config-file", "/bubble/etc/dbus-session.conf"]
        pid=self.api.start_process(args, ignore_status=False, extra_env=env, child_stdout_file=f"{tmpdir_bubble}/stdout.fifo")
        dbus_env={}
        counter=0
        last_e=None
        launch_result=None
        while True:
            time.sleep(0.1)
            try:
                status=self.api.get_process_status(pid)
                if status is not None:
                    launch_result=os.read(ofd, 1024)

                    if launch_result:
                        launch_result=launch_result.decode()
                        socket_name=None
                        socket_path=None
                        bus_address=None
                        bus_pid=None

                        # parse launch_result to see if we got all at once
                        for line in launch_result.splitlines():
                            if line.startswith("DBUS_SESSION_BUS_ADDRESS="):
                                (_, path)=line.split("=", maxsplit=1)

                                # define the env. variable in the bubble
                                m=re.search("'([^']*)", path)
                                if m is None:
                                    raise Exception(f"Could not parse dbus-launch output line {line}")
                                bus_address=m.group(1)

                                # get the host vision of the same path
                                m=re.search("/bubble/run/([^']*)", path)
                                if m is None:
                                    raise Exception(f"Could not parse dbus-launch output line {line}")
                                (socket_path, _)=os.path.join(os.path.realpath(self.run_dir), m.group(1)).split(",")
                                socket_name=f"unix:path={os.path.realpath(self.run_dir)}/{m.group(1)}"

                            elif line.startswith("DBUS_SESSION_BUS_PID="):
                                (_, value)=line.split("=", maxsplit=1)
                                m=re.search("([0-9]*)", value)
                                if m is None:
                                    raise Exception(f"Could not parse dbus-launch output line {line}")
                                bus_pid=m.group(1)

                        if socket_name is not None and bus_address is not None and bus_pid is not None:
                            self.api.declare_env_variable("DBUS_SESSION_BUS_ADDRESS", bus_address)
                            self._dbus_socket_path_in_host=socket_path
                            self.api.declare_env_variable("DBUS_SESSION_BUS_PID", bus_pid) # DBUS_SESSION_BUS_PID has no use outside of the bubble
                            dbus_env["DBUS_SESSION_BUS_ADDRESS"]=socket_name
                            break
            except Exception as e:
                last_e=e
                syslog.syslog(syslog.LOG_DEBUG, f"{self.syslog_prefix}: error while starting DBUS daemon (will retry): {str(e)}")
            counter+=1
            if counter>20:
                if last_e is None:
                    raise Exception(f"{self.syslog_prefix}: dbus-launch did not return the expected data, got: '{launch_result}'")
                raise last_e
        self._dbus_env_in_host=dbus_env

    def _start_dbus_router(self, options:list[padsi.config.ZoneOption]):
        """Start a DBus router instance in its own bubble
        """
        if self._dbus_socket_path_in_host is None:
            raise Exception("CODEBUG: self._dbus_socket_path_in_host should not be None")
        if self.api is None:
            raise Exception("CODEBUG: zone has not yet been started")

        dbus_router_socket_path=os.path.join(self.run_dir, "dbus-router", "router.socket")
        self._dbus_router=ZoneDBusRouter(zone=self.zone_conf, options=options, logs_dir=self.logs_dir,
                                         zone_dbus_socket_path=self._dbus_socket_path_in_host,
                                         dbus_router_socket_path=dbus_router_socket_path, run_dir=os.path.dirname(self.run_dir))
        self._dbus_router.setup()
        self.api.declare_env_variable("DBUS_SESSION_BUS_ADDRESS", "unix:path=/bubble/run/dbus-router/router.socket")

    def compute_mount_points(self) -> dict:
        mounts=super().compute_mount_points()
        mounts.update(get_apps_generic_mount_points(self._z_infra.wayland_proxy_socket if self._z_infra is not None else None))
        script_dir=os.path.dirname(os.path.realpath(__file__))

        if self._zone_service_socket is not None:
            mounts[self._zone_service_socket]={
                "mount-point": "/bubble/run/padsi-zserv.sock",
                "read-only": False,
                "monitored": False
            }

        # zone specific mount points
        mounts[self._zuf.zone_home_dir]={
            "mount-point": padsi.misc.get_user_home_dir(self._uid),
            "read-only": False,
            "monitored": False
        }

        # /etc/resolv.conf file
        if self._infra_dns_ip is not None:
            resolv_conf=f"{self.run_dir}/resolv.conf"
            with open(resolv_conf, "w") as fd:
                fd.write(f"nameserver {str(self._infra_dns_ip)}\n")
                fd.close()
            mounts[resolv_conf]={
                "mount-point": "/etc/resolv.conf",
                "read-only": True,
                "monitored": False
            }

        # CLI file
        mounts["/usr/share/padsi/padsi/cli/padsi-cli-zone"]={
            "mount-point": "/usr/bin/padsi-cli",
            "read-only": True,
            "monitored": False
        }

        # PKCS11 library
        pkcs11_option=self.zone_conf.get_option(padsi.config.ZoneOptionType.PKCS11)
        if pkcs11_option.enabled:
            pkcs11_option=padsi.config.PKCS11Option.downcast(pkcs11_option)
            if pkcs11_option.driver_path is not None:
                if not pkcs11_option.driver_path.startswith("/usr") and not pkcs11_option.driver_path.startswith("/lib"): # pyright: ignore
                    # FIXME: also add DLL dependencies (use 'ldd')
                    mounts[pkcs11_option.driver_path]={
                        "mount-point": pkcs11_option.driver_path,
                        "read-only": True,
                        "monitored": False
                    }

        # FIDO2 usage
        fido2_option=self.zone_conf.get_option(padsi.config.ZoneOptionType.FIDO2)
        if fido2_option.enabled:
            # programs need access to have access to /sys to perform udev enumration and /run/udev for hotplug detection
            # beyond access to /dev/hidraw*
            mounts["/sys"]={
                "mount-point": "/sys",
                "read-only": True,
                "monitored": False
            }
            mounts["/run/udev"]={
                "mount-point": "/run/udev",
                "read-only": True,
                "monitored": False
            }

            # access to the netlink host helper
            mounts[f"/run/user/{self._uid}/padsi-netlink.sock"]={
                "mount-point": "/bubble/run/padsi-netlink.sock",
                "read-only": False,
                "monitored": False
            }

            # set up LD_PRELOAD for the netlink shim
            shim_lib=os.path.realpath(os.path.join(script_dir, "..", "..", "bin", "netlink-shim.so"))
            if not os.path.isfile(shim_lib):
                raise Exception(f"Netlink shim library '{shim_lib}' is missing")
            preload_file=os.path.join(self.run_dir, "netlink.preload")
            with open(preload_file, "wt") as fd:
                fd.write(f"{shim_lib}\n")
            mounts[preload_file]={
                "mount-point": "/etc/ld.so.preload",
                "read-only": True,
                "monitored": False
            }

        # policies directories for the programs for which policies can be defined
        factory=padsi.config.ProgramPoliciesFactory()
        all_pol_dirs:set[str]=set()
        for progname in factory.supported_programs:
            policies=factory.get_program_policies(progname)
            if policies is not None:
                base_pol_dir=os.path.join(self.run_dir, "policies", progname)
                for dirname in policies.get_writable_directories():
                    if dirname not in all_pol_dirs:
                        if dirname[0]!="/":
                            syslog.syslog(syslog.LOG_WARNING, f"CODEBUG: {self.syslog_prefix}: writable directory '{dirname}' for {progname}'s policies should be absolute")
                        else:
                            dirname=dirname[1:]
                        fdirname=f"/{dirname}"
                        pol_dir=os.path.join(base_pol_dir, dirname)
                        all_pol_dirs.add(dirname)
                        os.makedirs(pol_dir, exist_ok=True)
                        mounts[pol_dir]={
                            "mount-point": fdirname,
                            "read-only": False,
                            "monitored": False
                        }

                        # copy any host settings to the zone
                        if os.path.exists(fdirname):
                            shutil.copytree(fdirname, pol_dir, dirs_exist_ok=True)
        return mounts

    @property
    def features(self) -> nsbubble.Features:
        bind_medias=self.zone_conf.get_option(padsi.config.ZoneOptionType.MEDIAS).enabled
        with_drm=self.zone_conf.get_option(padsi.config.ZoneOptionType.DRM).enabled
        with_fuse=self.zone_conf.get_option(padsi.config.ZoneOptionType.FUSE).enabled
        bind_medias=self.zone_conf.get_option(padsi.config.ZoneOptionType.MEDIAS).enabled
        with_pcscd=self.zone_conf.get_option(padsi.config.ZoneOptionType.PKCS11).enabled or \
            self.zone_conf.get_option(padsi.config.ZoneOptionType.GPG_CARD).enabled
        with_fido2=self.zone_conf.get_option(padsi.config.ZoneOptionType.FIDO2).enabled
        return nsbubble.Features(bind_x11=self.with_x11, with_multimedia=True, with_syslog=True, with_host_resolv=False,
                                   extra_env=self._extra_env, bind_medias=bind_medias,
                                   with_drm=with_drm, with_fuse=with_fuse, with_pcscd=with_pcscd,
                                   bind_dev=with_fido2,
                                   display_env={
                                        "WAYLAND_DISPLAY": "wayland-0"
                                   })

    def _apply_policies(self, zuf:ZoneUserFiles):
        factory=padsi.config.ProgramPoliciesFactory()

        # (re) initialize any policy located in the system files (the ones located in the HOME directory
        # have been handled when the ZoneUserFiles was set up)
        for progname in factory.supported_programs:
            syslog.syslog(syslog.LOG_DEBUG, f"{self.syslog_prefix}: (Re) initializing (system) policies for '{progname}'")
            policies=factory.get_program_policies(progname)
            if policies is not None:
                base_pol_dir=os.path.join(self.run_dir, "policies", progname)
                try:
                    policies.initialize_policies(system_dir=base_pol_dir)
                except Exception as e:
                    syslog.syslog(syslog.LOG_ERR, f"{self.syslog_prefix}: failed to initialize (system) policies for {progname}: {str(e)}")

        # extra ROOT CA certificate for browsers
        if self._extra_root_cert is not None:
            for progname in factory.supported_browsers:
                syslog.syslog(syslog.LOG_DEBUG, f"{self.syslog_prefix}: adding extra CA cert. for '{progname}'")
                policies=factory.get_program_policies(progname)
                if policies is not None:
                    base_pol_dir=os.path.join(self.run_dir, "policies", progname)
                    try:
                        policies.add_trusted_ca(base_pol_dir, zuf.zone_home_dir, "Web redirection CA", self._extra_root_cert)
                    except Exception as e:
                        syslog.syslog(syslog.LOG_ERR, f"{self.syslog_prefix}: failed to add Root CA to {progname}: {str(e)}")

        pki_option=self.zone_conf.get_option(padsi.config.ZoneOptionType.PKI)
        if pki_option.enabled:
            pki_option=padsi.config.PKIOption.downcast(pki_option)
            for progname in factory.supported_browsers:
                policies=factory.get_program_policies(progname)
                if policies is not None:
                    base_pol_dir=os.path.join(self.run_dir, "policies", progname)
                    for (nickname, ca_cert) in pki_option.ca_certs.items():
                        try:
                            syslog.syslog(syslog.LOG_DEBUG, f"{self.syslog_prefix}: adding trusted CA '{nickname}' for '{progname}'")
                            policies.add_trusted_ca(base_pol_dir, zuf.zone_home_dir, nickname, ca_cert)
                        except Exception as e:
                            syslog.syslog(syslog.LOG_ERR, f"{self.syslog_prefix}: failed to add trusted CA '{nickname}' to {progname}: {str(e)}")

        pkcs11_option=self.zone_conf.get_option(padsi.config.ZoneOptionType.PKCS11)
        if pkcs11_option.enabled:
            pkcs11_option=padsi.config.PKCS11Option.downcast(pkcs11_option)
            for progname in factory.supported_browsers:
                syslog.syslog(syslog.LOG_DEBUG, f"{self.syslog_prefix}: adding PKCS#11 driver for '{progname}'")
                policies=factory.get_program_policies(progname)
                if policies is not None and pkcs11_option.driver_name is not None and pkcs11_option.driver_path is not None:
                    base_pol_dir=os.path.join(self.run_dir, "policies", progname)
                    try:
                        policies.add_pkcs11_driver(base_pol_dir, zuf.zone_home_dir, pkcs11_option.driver_name, pkcs11_option.driver_path)
                    except Exception as e:
                        syslog.syslog(syslog.LOG_ERR, f"{self.syslog_prefix}: failed to add PKCS#11 driver '{pkcs11_option.driver_path}' to {progname}: {str(e)}")

    def start(self):
        """Actually start the bubble
        """
        # apply policies and specific options
        self._apply_policies(self._zuf)

        if self.with_x11:
            # run xlsclients to force XWayland to start if not yet done
            denv:nsbubble.DisplayEnvironment=nsbubble.get_display_env()
            if denv.x11_display is not None and denv.x11_auth is not None:
                cenv=os.environ.copy()
                cenv["DISPLAY"]=denv.x11_display
                cenv["XAUTHORITY"]=denv.x11_auth
                x=subprocess.run(["xlsclients"], env=cenv, capture_output=True)
                if x.returncode!=0:
                    syslog.syslog(syslog.LOG_WARNING, f"{self.syslog_prefix}: failed to force starting of XWayland: {x.stderr.decode()}")
            else:
                syslog.syslog(syslog.LOG_WARNING, f"{self.syslog_prefix}: failed to force starting of XWayland: determined DISPLAY={denv.x11_display}, XAUTHORITY={denv.x11_auth}")

        super().start()
        self._start_zone_dbus()

        router_enabled=False
        options:list[padsi.config.ZoneOption]=[
            self.zone_conf.get_option(padsi.config.ZoneOptionType.SCREEN_SHARE),
            self.zone_conf.get_option(padsi.config.ZoneOptionType.DESKTOP_NOTIFICATIONS)
        ]
        for option in options:
            if option.enabled:
                router_enabled=True
        if router_enabled:
            self._start_dbus_router(options)

        syslog.syslog(syslog.LOG_DEBUG, f"{self.syslog_prefix}: started")

    @property
    def dbus_env(self):
        if self.bubble is None:
            raise Exception("dbus_env property: zone has not yet been started")
        return self._dbus_env_in_host

    def has_gui_processes(self) -> bool:
        """Tell if there are some processes in the zone which have a potential GUI
        """
        # This feature is not yet implemented because there is currently no reliable way to map which program
        # has a socket opened to the Wayland server or the X11 server as they are in different network namespaces.
        # Future work based on either LD_PRELOAD or eBPF to monitor sockets' usage might be able to bridge that gap.
        return True

    @classmethod
    def prepare_dirs(cls, gconf:padsi.config.Configuration, zone_name:str, vm_dir:str|None, uid:int, gid:int):
        # ensure the home directory for the user and the zone actually exists
        for dirname in [gconf.get_zone_user_home_dir(uid, zone_name)]:
            os.makedirs(dirname, exist_ok=True)
            os.chown(dirname, uid, gid)
            os.chmod(dirname, 0o700)

        # create directories to hold non tmp resources
        for name in ("applications", "home",  "icons",  "log", "VM"):
            path=os.path.join(gconf.var_dir, name)
            os.makedirs(path, mode=0o755, exist_ok=True)

        # create per user logs directory, per user, like "/var/padsi/log/<UID>/<zone name>"
        # and set the permissions and ownership accordingly
        logs_dir=gconf.get_zone_logs_dir(zone_name, uid)
        os.makedirs(logs_dir, mode=0o755, exist_ok=True)

        plogs_dir=os.path.dirname(logs_dir)
        try:
            # quick check
            if int(os.path.basename(plogs_dir))!=uid:
                raise Exception()
        except Exception:
            raise Exception(f"CODEBUG: Logs dir '{logs_dir}' for UID {uid} should contain the UID of the user")
        os.chown(plogs_dir, uid, gid)
        os.chmod(plogs_dir, 0o700)

        os.chown(logs_dir, uid, gid)
        os.chmod(logs_dir, 0o700)

        # create VM directories
        if vm_dir is not None:
            os.makedirs(vm_dir, mode=0o755, exist_ok=True)
            staging=os.path.join(vm_dir, "staging")
            os.makedirs(staging, mode=0o755, exist_ok=True)
            os.chmod(logs_dir, 0o777)

            vmfiles=VMFiles(vm_dir, uid, gid)
            for dir in (vmfiles.get_zone_directory(zone_name),
                        vmfiles.staging_directory):
                os.makedirs(dir, mode=0o755, exist_ok=True)
                os.chown(dir, uid, gid)
                os.chmod(dir, 0o700)

def get_apps_generic_mount_points(wayland_proxy_socket:str|None) -> dict:
    script_dir=os.path.dirname(os.path.realpath(__file__))
    mounts={
        os.path.join(script_dir, "etc"): {
            "mount-point": "/bubble/etc",
            "read-only": True,
            "monitored": False
        }
    }

    if wayland_proxy_socket is not None:
        # bind mount the Wayland proxy's socket
        mounts[wayland_proxy_socket]={
            "mount-point": "/bubble/run/wayland-0",
            "read-only": False,
            "monitored": False
        }
    else:
        # bind mount the host's Wayland compositor's socket
        denv:nsbubble.DisplayEnvironment=nsbubble.get_display_env()
        if denv.runtime_dir and denv.wayland_display:
            mounts[os.path.join(denv.runtime_dir, denv.wayland_display)]={
                "mount-point": "/bubble/run/wayland-0",
                "read-only": False,
                "monitored": False
            }

    # extra mount points for some applications
    for romp in ("/etc/alternatives", "/etc/chromium.d", "/etc/gimp", "/etc/libreoffice", "/opt", "/usr/games", "/var/lib/flatpak"):
        if os.path.isdir(romp):
            mounts[romp]={
                "mount-point": romp,
                "read-only": True,
                "monitored": False
            }
    return mounts
