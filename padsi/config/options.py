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
import os
from dataclasses import dataclass


class ZoneOptionType(str, enum.Enum):
    DESKTOP_NOTIFICATIONS = "DESKTOP-NOTIFICATIONS"  # allow to show notifications
    DRM = "DRM"  # allow access to DRM (/dev/dri)
    FIDO2 = "FIDO2"  # allow the usage of FIDO2 authenticators
    FUSE = "FUSE"  # allow access to FUSE based filesystem mounting
    MEDIAS = "MEDIAS"  # allow access to /media/<username>/XXX where mass storage medias are mounted
    NET_LOG_ONLY = "NET-LOG-ONLY"  # deny network accesses are only logged, not actually denied
    PKCS11 = "PKCS11"  # allow the usage of smartcards
    GPG_CARD = "GPG-CARD" # allow the usage of GPG cards
    PKI = "PKI"  # trusted CA certificates
    SCREEN_SHARE = "SCREEN-SHARE"  # allow screen sharing
    WEB_REDIRECTION = "WEB-REDIRECTION"  # allow web sites to be opened in the web browser of a zone where the URL is accessible
    X11 = "X11"  # zone allows X11 applications
    DNS_BLOCKLIST = "DNS-BLOCKLIST" # block list for DNS resolutions, refer to https://github.com/StevenBlack/hosts


@dataclass
class ZoneOption:
    option_type: ZoneOptionType
    enabled: bool

    @classmethod
    def from_data(cls, option_type: ZoneOptionType, data, config_dir: str | None = None) -> ZoneOption:
        try:
            match option_type:
                case (
                    ZoneOptionType.X11
                    | ZoneOptionType.NET_LOG_ONLY
                    | ZoneOptionType.SCREEN_SHARE
                    | ZoneOptionType.DESKTOP_NOTIFICATIONS
                    | ZoneOptionType.MEDIAS
                    | ZoneOptionType.DRM
                    | ZoneOptionType.FIDO2
                    | ZoneOptionType.GPG_CARD
                    | ZoneOptionType.FUSE
                ):
                    if not isinstance(data, bool):
                        raise Exception("expected a boolean")
                    return BoolOption(option_type, data)

                case ZoneOptionType.WEB_REDIRECTION:
                    zones = data["zones"]
                    if not isinstance(zones, list):
                        raise Exception()
                    for item in zones:
                        if not isinstance(item, str):
                            raise Exception()
                    return WebRedirectionOption(option_type, True, allowed_zones=data["zones"])

                case ZoneOptionType.PKI:
                    if not isinstance(data, dict):
                        raise Exception("expected a dictionary")
                    certs = {}
                    for name, fpath in data.items():
                        name = name.strip()
                        if (
                            not isinstance(name, str)
                            or not name
                            or not isinstance(fpath, str)
                        ):
                            raise Exception("expected <CA nickname>:<CA certificate path>")
                        if not os.path.isabs(fpath):
                            if config_dir is None:
                                raise Exception("CA certificate path is not absolute and config_dir not set")
                            fpath = os.path.join(config_dir, fpath)
                        if not os.path.isfile(fpath):
                            raise Exception(f"CA certificate path '{fpath}' does not exist")
                        with open(fpath, "rt") as fd:
                            certs[name] = fd.read()
                    return PKIOption(option_type, True, certs)

                case ZoneOptionType.PKCS11:
                    if not isinstance(data, dict):
                        raise Exception("expected a dictionary")
                    name = data["driver-name"]
                    if not isinstance(name, str):
                        raise Exception()
                    driver = data["driver-file"]
                    if not os.path.isfile(driver):
                        raise Exception(f"PKCS#11 driver file '{driver}' does not exist")
                    return PKCS11Option(option_type, True, driver_name=name, driver_path=driver)

                case ZoneOptionType.DNS_BLOCKLIST:
                    if not isinstance(data, str):
                        raise Exception("Expected a file path")
                    if not os.path.isabs(data) and config_dir is not None:
                        data=os.path.join(config_dir, data)
                    if not os.path.isfile(data):
                        raise Exception(f"Block list file '{data}' does not exist")
                    return BlockListOption(option_type, enabled=True, blocklist_file=data)

                case _:
                    raise Exception(f"CODEBUG: unhandled ZoneOptionType '{option_type}'")
        except Exception as e:
            raise Exception(f"Invalid data for '{option_type.value}' option: {str(e)}")


@dataclass
class BoolOption(ZoneOption):
    def __repr__(self) -> str:
        return "enabled" if self.enabled else "disabled"


@dataclass
class WebRedirectionOption(ZoneOption):
    allowed_zones: list[str]  # zones where a redirection can be made

    def __repr__(self) -> str:
        if self.enabled:
            return ", ".join(self.allowed_zones)
        return "disabled"


@dataclass
class PKIOption(ZoneOption):
    ca_certs: dict[str, str]  # key=cert. nickname, value=PEM encoded cert

    def __repr__(self) -> str:
        if self.enabled:
            return ", ".join(self.ca_certs.keys())
        return "disabled"

    def downcast(self:ZoneOption) -> PKIOption:
        return self # type: ignore[return-value]


@dataclass
class PKCS11Option(ZoneOption):
    driver_name: str | None
    driver_path: str | None  # path to the PKCS11 library, e.g. from opensc-pkcs11

    def __repr__(self) -> str:
        if self.enabled:
            return f"'{self.driver_name}' ({self.driver_path})"
        return "disabled"

    def downcast(self:ZoneOption) -> PKCS11Option:
        return self # type: ignore[return-value]

@dataclass
class FIDO2Option(ZoneOption):
    # maybe later add authenticator filtering
    def __repr__(self) -> str:
        if self.enabled:
            return "enabed (TODO)"
        return "disabled"

@dataclass
class BlockListOption(ZoneOption):
    blocklist_file: str|None

    def __repr__(self) -> str:
        if self.enabled:
            assert(self.blocklist_file is not None)
            return self.blocklist_file
        return "disabled"

    def downcast(self:ZoneOption) -> BlockListOption:
        return self # type: ignore[return-value]
