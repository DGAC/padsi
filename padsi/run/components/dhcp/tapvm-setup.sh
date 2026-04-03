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


#
# This script sets up the VM's TAP network interface which will
# handle DHCP requests from the VM
#

iface="$1"
ip="$2"
mtu="$3" # may be ""

# log everything
exec 1> >(logger -s -t $(basename $0)) 2>&1

function wait_for_iface() {
    local if="$1"
    for count in $(seq 1 20);
    do
        ip a show "$if" >/dev/null 2>&1 && {
            return
        }
        sleep 0.5
    done
    echo "Failed to wait for the '$if' network interface"
    exit 2
}

set -e
wait_for_iface "$iface"
ip addr add "$ip" dev "$iface"
ip link set up dev "$iface"
[ "$mtu" == "" ] || {
    ip link set dev "$iface" mtu "$mtu"
}
