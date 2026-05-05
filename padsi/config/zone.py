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

from __future__ import annotations

import enum
import json
import os
import re
import syslog

from . import network, vm
from .mountpoint import MountPoint
from .options import (BlockListOption, BoolOption, StrStrDictOption, FIDO2Option, PKCS11Option,
                      PKIOption, VMOnlyOption, WebRedirectionOption, ZoneOption,
                      ZoneOptionType)
from .proxy import Proxy
from .trafficshaper import TrafficShaper

_debug = False


def _parse_color(color: str) -> list[float]:
    """Parse color specifications.
    Expected format:
    - #abcdef
    """
    try:
        if color[0] != "#" or len(color) != 7:
            raise Exception()
        r = int(color[1:3], 16)
        g = int(color[3:5], 16)
        b = int(color[5:7], 16)
        return [r / 255, g / 255, b / 255]
    except Exception:
        syslog.syslog(syslog.LOG_WARNING, f"Invalid color specification '{color}', using RED")
        return [0.8, 0, 0]


class StartMode(str, enum.Enum):
    ON_DEMAND = "ON-DEMAND"  # zone infra and zone apps started on demand
    ALWAYS_INFRA = "ALWAYS-INFRA" # zone infra always started
    ALWAYS = "ALWAYS" # zone infra and zone apps always started

class Zone:
    """Contains a single zone's configuration"""

    def __init__(
        self,
        name: str,
        start_mode: StartMode,
        friendly_name: str | None,
        color: str | None,
        options: dict[ZoneOptionType, ZoneOption],
        net: network.NetworkSpec | None,
        mounts: list[MountPoint] | None,
        apps: list[str],
        vms: dict[str, vm.VirtualMachine],
        proxies: list[Proxy] | None,
        raw_data: dict,
        config_dir: str,
    ):
        self._name = name
        self._friendly_name = friendly_name
        self._start_mode = start_mode
        self._color_str = color
        self._color = _parse_color(color) if color is not None else None
        self._options = options
        self._mounts = mounts
        self._network = net
        self._apps = apps
        self._vm_list = vms
        self._web_proxies = proxies if proxies is not None else []
        self._raw_data = raw_data
        self._config_dir = config_dir

    @classmethod
    def from_data(
        cls,
        name: str,
        data: dict,
        named_netres: dict[str, network.NetworkRessources] | None,
        traffic_shapers: dict[str, TrafficShaper],
        run_vms: dict[str, vm.VirtualMachine],
        config_dir: str,
    ) -> Zone:
        # zone's attributes
        if name == "XDG" or not re.match(r"^[a-z][a-z0-9]+", name):
            raise Exception(f"invalid zone name '{name}'")

        try:
            start_mode = StartMode(data["start-mode"].upper())
        except Exception:
            raise Exception(f"Invalid start-mode attribute '{data.get('start-mode')}")

        friendly_name = data.get("friendly-name")
        if friendly_name is not None and not isinstance(friendly_name, str):
            raise Exception(f"Invalid friendly-name attribute '{friendly_name}")

        color = data.get("color")
        if color is not None and not isinstance(color, str):
            raise Exception(f"Invalid color attribute '{color}")

        # options
        options = {}
        opts = data.get("options", {})
        if not isinstance(opts, dict):
            raise Exception("Invalid 'options' section")
        for k, v in opts.items():
            try:
                if not isinstance(k, str):
                    raise Exception()
                ok = ZoneOptionType(k)
            except Exception:
                raise Exception(f"Invalid option '{k}'")
            try:
                options[ok] = ZoneOption.from_data(ok, v, config_dir=config_dir)
            except Exception:
                raise Exception(f"Invalid configuration for option '{k}'")

        # web proxies
        proxy_data=data.get("web-proxy")
        proxies:list[Proxy]=[]
        if proxy_data is not None:
            if not isinstance(proxy_data, list):
                raise Exception("Invalid 'web-proxy' section")
            for proxy_conf in proxy_data:
                proxy = Proxy.from_data(proxy_conf, named_netres)
                if proxy is not None:
                    proxies.append(proxy)

        # network
        net = network.NetworkSpec.from_data(data.get("network"), named_netres, traffic_shapers, config_dir=config_dir)

        # mount points
        decl_mp = data.get("mounts")
        if not isinstance(decl_mp, dict):
            raise Exception("Invalid 'mounts' section")
        mounts = MountPoint.load_from_data(decl_mp, allow_absolute_destination_path=True)

        # apps
        apps = data.get("apps", [])
        if apps is None:
            apps=[]
        if not isinstance(apps, list):
            raise Exception("Invalid 'apps' section")
        for app in apps:
            if not isinstance(app, str):
                raise Exception(f"Invalid application '{app}' 'apps' section")

        # virtual machines
        vms = {}
        vmdata = data.get("virtual-machines") or {}
        for vmid, vmddata in vmdata.items():
            # build a VirtualMachine from the global configuration's VM with the same ID
            base_run_vm = run_vms.get(vmid)
            if base_run_vm is None:
                raise Exception(f"Unknown VM '{vmid}'")
            vms[vmid] = base_run_vm.specialize(vmddata, named_netres)

        # checks
        opt=options.get(ZoneOptionType.VM_ONLY)
        if opt is not None and opt.enabled:
            opt=VMOnlyOption.downcast(opt)
            if opt.default_vm not in vms:
                raise Exception (f"VM-ONLY's default VM '{opt.default_vm}' is not available in zone")
            if start_mode==StartMode.ALWAYS:
                start_mode=StartMode.ALWAYS_INFRA

        return cls(name, start_mode, friendly_name, color, options, net, mounts,
            apps, vms, proxies, data, config_dir)

    @property
    def name(self) -> str:
        return self._name

    @property
    def friendly_name(self) -> str:
        return self._friendly_name if self._friendly_name else self._name

    @property
    def config_dir(self) -> str:
        """Directory containing the zone's config file"""
        return self._config_dir

    @property
    def start_mode(self) -> StartMode:
        return self._start_mode

    @start_mode.setter
    def start_mode(self, start_mode: StartMode):
        self._start_mode = start_mode

    @property
    def color(self) -> list[float] | None:
        return self._color

    @property
    def color_str(self) -> str | None:
        return self._color_str

    @property
    def mount_points(self) -> list[MountPoint]:
        """Get the list of mount points configured in the zone"""
        return self._mounts if self._mounts is not None else []

    @property
    def network_spec(self) -> network.NetworkSpec | None:
        return self._network

    @property
    def network_enabled(self) -> bool:
        return self._network is not None

    @property
    def traffic_shaper(self) -> TrafficShaper | None:
        return None if self._network is None else self._network.traffic_shaper

    @property
    def web_proxies(self) -> list[Proxy]:
        """Get the Web proxies to be used by programs in the zone (list may be empty)"""
        return self._web_proxies

    def get_proxy(self, proxy_ip_port:str) -> Proxy|None:
        for proxy in self._web_proxies:
            if f"{proxy.host}:{proxy.port}"==proxy_ip_port:
                return proxy
        return None

    @property
    def apps(self) -> list[str]:
        return self._apps

    @property
    def has_dns_resolution(self) -> bool:
        """Tell if the zone includes a DNS resolution service,
        either because it uses some resolvers or because it offers static resolution
        """
        if self._network is None:
            return False
        return (self._network.resolvers is not None or self._network.resolv_rules is not None)

    @property
    def has_host_dns_resolvers(self) -> bool:
        """Tell if the zone uses the host's DNS resolvers configuration"""
        if self._network is None:
            return False
        return self._network.resolvers == []

    @property
    def dns_resolvers(self) -> list[network.DNSEndpoint] | None:
        """Get the list of DNS resolvers:
        - None to not have any DNS resolution service, or only static resolution
        - [] (empty list) to use the host's resolvers
        - a non empty list to use a static list of DNS resolvers
        """
        return None if self._network is None else self._network.resolvers

    @property
    def fw_rules(self) -> list[network.FWRule] | None:
        return None if self._network is None else self._network.fw_rules

    @property
    def resolv_rules(self) -> list[network.ResolvRule] | None:
        return None if self._network is None else self._network.resolv_rules

    @property
    def has_virtual_machines(self) -> bool:
        """Tell if the zone can have virtual machines at all"""
        return len(self._vm_list) > 0

    def get_virtual_machine_ids(self) -> list[str]:
        """Get the list of all virtual machines IDs which can be executed in the zone"""
        return list(self._vm_list.keys())

    def get_virtual_machine(self, vmid: str) -> vm.VirtualMachine | None:
        """Get the definition of a specific virtual machine allowed in the zone"""
        return self._vm_list.get(vmid)

    @property
    def configured_options(self) -> dict[ZoneOptionType, ZoneOption]:
        return self._options

    def get_option(self, option: ZoneOptionType) -> ZoneOption:
        """Get the value of an option"""
        _default = {
            ZoneOptionType.X11: BoolOption(ZoneOptionType.X11, False),
            ZoneOptionType.NET_LOG_ONLY: BoolOption(ZoneOptionType.NET_LOG_ONLY, False),
            ZoneOptionType.SCREEN_SHARE: BoolOption(ZoneOptionType.SCREEN_SHARE, False),
            ZoneOptionType.DESKTOP_NOTIFICATIONS: BoolOption(ZoneOptionType.DESKTOP_NOTIFICATIONS, False),
            ZoneOptionType.MEDIAS: BoolOption(ZoneOptionType.MEDIAS, False),
            ZoneOptionType.DRM: BoolOption(ZoneOptionType.DRM, False),
            ZoneOptionType.FUSE: BoolOption(ZoneOptionType.FUSE, False),
            ZoneOptionType.WEB_REDIRECTION: WebRedirectionOption(ZoneOptionType.WEB_REDIRECTION, False, []),
            ZoneOptionType.PKI: PKIOption(ZoneOptionType.PKI, False, {}),
            ZoneOptionType.PKCS11: PKCS11Option(ZoneOptionType.PKCS11, False, None, None),
            ZoneOptionType.GPG_CARD: BoolOption(ZoneOptionType.FUSE, False),
            ZoneOptionType.FIDO2: FIDO2Option(ZoneOptionType.FIDO2, False),
            ZoneOptionType.DNS_BLOCKLIST: BlockListOption(ZoneOptionType.DNS_BLOCKLIST, False, None),
            ZoneOptionType.MOUNT_POINTS: StrStrDictOption(ZoneOptionType.MOUNT_POINTS, False, {}),
            ZoneOptionType.VM_ONLY: VMOnlyOption(ZoneOptionType.VM_ONLY, False, None)
        }
        if option not in _default:
            raise Exception(f"CODEBUG: option '{option}' does not have any default value")
        return self._options.get(option, _default[option])  # pyright: ignore

    def serialize(self) -> dict:
        return self._raw_data


def load_zone_file(
    config_dir: str,
    file_name: str,
    named_netres: dict[str, network.NetworkRessources] | None,
    traffic_shapers: dict[str, TrafficShaper],
    run_vms: dict[str, vm.VirtualMachine],
) -> Zone:
    path = os.path.join(config_dir, file_name)
    with open(path, "r") as fd:
        data = json.load(fd)
        return Zone.from_data(
            os.path.basename(path)[:-5], # remove .zone extension
            data,
            named_netres,
            traffic_shapers,
            run_vms,
            config_dir,
        )
