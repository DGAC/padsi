#!/bin/bash

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

set -e

# get the physical network interface, no "lo" or "docker*"
function get_first_interface() {
    for iface in /sys/class/net/*
    do
        liface=$(readlink "$iface")
        [[ "$liface" =~ virtual ]] || {
            echo $(basename "$iface")
            return
        }
    done
}

# tell if the specified network interface is already configured
function interface_configured() {
    local iface="$1"
    ip a show "$iface" | grep -q " inet " && echo "true" || echo "false"
}

# tell if the specified network interface is managed via the ifup/ifdown scripts
function interface_managed() {
    local iface="$1"
    ifquery "$iface" && echo "true"  || echo "false"
}


# determine interface to use
iface=$(get_first_interface)
[ "$iface" == "" ] && {
    logger -p user.err "Could not identify network interface to use"
    exit 1
}
logger -p user.info "Identified physical network interface $iface"

managed=$(interface_managed "$iface")
[ "$managed" == "true" ] && {
    logger -p user.info "Interface $iface is managed by ifup/ifdown (/etc/network/interfaces*), not running the dhcp client"
    exit 0
}

# configure interface if not yet done
configured=$(interface_configured "$iface")
[ "$configured" == "false" ] && {
    logger -p user.info "Configuring network interface $iface via DHCP"
    command dchlient >/dev/null 2>&1 && {
        dhclient "$iface"
    } || {
        dhcpcd -b -w -p -n "$iface"
    }

    true
} || {
    logger -p user.info "Network interface $iface is already configured"
}

exit 0
