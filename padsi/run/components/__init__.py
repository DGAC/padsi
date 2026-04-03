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

import syslog
from abc import ABC

import nsbubble


class Component(ABC):
    """Abstract class for all the components
    """
    @property
    def name(self) -> str:
        return str(self.__class__)

    def get_mountpoints(self) -> dict|None:
        """Get the mount points required by the component
        Cf. nsbubble's documentation for the formalism
        """
        return None

    def get_required_user_entry(self) -> str|None:
        """Specific user which may be required by the component
        as a complete GECOS line
        """
        return None

    def get_required_group_entry(self) -> str|None:
        """Specific users group which may be required by the component
        as a complete groups line
        """
        return None

    @property
    def capabilities(self) -> list[str]|None:
        """List all the capabilities required by the component"""
        return None

    def start(self, api:nsbubble.BubbleAPI):
        """Actually start the component's processes in a bubble using the api object
        """
        pass

    def stop(self, api:nsbubble.BubbleAPI):
        """Actually stop the component's processes and remove any artefacts left
        """
        pass

    def serialize(self) -> dict:
        """Serialize the component for the purpose of calling the stop() method,
        should only include the required attributes"""
        return {}

    @classmethod
    def deserialize(cls, data:dict) -> Component:
        """Deserialize the component"""
        match data.get("class"):
            case "DNSServer":
                from .dns import DNSServer
                return DNSServer.deserialize(data)
            case "DHCPServer":
                from .dhcp import DHCPServer
                return DHCPServer.deserialize(data)
            case "FUSE":
                from .fuse import Fuse
                return Fuse.deserialize(data)
            case "FWLogger":
                from .fw_logger import FWLogger
                return FWLogger.deserialize(data)
            case "StaticFirewall":
                from .static_firewall import StaticFirewall
                return StaticFirewall.deserialize(data)
            case "USBRedir":
                from .usbredir import USBRedir
                return USBRedir.deserialize(data)
            case "VirtioFSServer":
                from .virtiofs import VirtioFSServer
                return VirtioFSServer.deserialize(data)
            case "VMMonitor":
                from .vm_monitor import VMMonitor
                return VMMonitor.deserialize(data)
            case "WebInfra":
                from .web_infra import WebInfra
                return WebInfra.deserialize(data)
            case "WaylandProxy":
                from .wayland_proxy import WaylandProxy
                return WaylandProxy.deserialize(data)
            case _:
                msg=f"Unhandled deserialization of component class '{data.get('class')}'"
                syslog.syslog(syslog.LOG_ERR, msg)
                raise Exception(msg)
