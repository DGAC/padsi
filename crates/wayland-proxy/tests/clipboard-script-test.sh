#!/bin/bash

#
# Copyright (c) 2026 DGAC/DSNA
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

zone1_name="secure"
zone2_name="UNSECURE"
unset DISPLAY

script_dir=$(realpath "$0")
script_dir=$(dirname "$script_dir")

echo "Building debug version"
cargo build || exit 1

trap cleanups EXIT
function cleanups() {
    for pid in $zone1_client_pid $zone2_client_pid $zone1_proxy_pid $zone2_proxy_pid; do
        kill $zone1_client_pid 2>/dev/null
    done
    rm *.sock
    exit 0
}

# cleanups from previous run
rm -rf logs-*

# starting Wayland proxies
mkdir -p "$script_dir/logs-${zone1_name}"
LOG_DIR="$script_dir/logs-${zone1_name}" RUST_BACKTRACE=1 cargo run -- /run/user/1000/wayland-0 "$script_dir/wayland-proxy-${zone1_name}.sock" "${zone1_name}" "${zone2_name}" &
zone1_proxy_pid=$!
echo "${zone1_name} proxy's PID: $zone1_proxy_pid"

mkdir -p "$script_dir/logs-${zone2_name}"
LOG_DIR="$script_dir/logs-${zone2_name}" RUST_BACKTRACE=1 cargo run -- /run/user/1000/wayland-0 "$script_dir/wayland-proxy-${zone2_name}.sock" "${zone2_name}" &
zone2_proxy_pid=$!
echo "${zone2_name} proxy's PID: $zone2_proxy_pid"

sleep 0.5

# test 1: copy/paste is blocked
cdata="Copied from $zone1_name"
WAYLAND_DISPLAY="$script_dir/wayland-proxy-${zone1_name}.sock" wl-copy --f --type text/plain "$cdata" &
zone1_client_pid=$!
sleep 0.25

pdata=$(WAYLAND_DISPLAY="$script_dir/wayland-proxy-${zone2_name}.sock" wl-paste)
echo "received: [$pdata]"
[ "$pdata" == "" ] || {
    echo "Failed test 1: pasted '$pdata', expected '$cdata'"
    exit 1
}

# test 1: copy/paste is allowed
cdata="Copied from $zone2_name"
WAYLAND_DISPLAY="$script_dir/wayland-proxy-${zone2_name}.sock" wl-copy --f --type text/plain "$cdata" &
zone2_client_pid=$!
sleep 0.25

pdata=$(WAYLAND_DISPLAY="$script_dir/wayland-proxy-${zone1_name}.sock" wl-paste)
echo "received: [$pdata]"
[ "$pdata" == "$cdata" ] || {
    echo "Failed test 2: pasted '$pdata', expected '$cdata'"
    exit 1
}

echo "Ok"
