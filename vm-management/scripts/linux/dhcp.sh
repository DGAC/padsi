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

function get_first_interface() {
    for iface in /sys/class/net/*
    do
        [ "$iface" != "lo" ] && {
            echo $(basename "$iface")
            return
        }
    done
}

function interface_configured() {
    ip a show "$1" | grep -q " inet " && echo "true" || echo "false"
}

# determine interface to use
iface=$(get_first_interface)
[ "$iface" == "" ] && {
    logger -p user.err "Could not identify network interface to use"
    exit 1
}
logger -p user.info "Using network interface $iface"

# configure interface if not yet done
configured=$(interface_configured "$iface")
[ "$configured" == "false" ] && {
    logger -p user.info "Configuring network interface $iface via DHCP"
    command dchlient >/dev/null 2>&1 && {
        dhclient "$iface"
    } || {
        dhcpcd -q -b -w -p -n "$iface"
    }

    true
} || {
    logger -p user.info "Network interface $iface is already configured"
}

exit 0
