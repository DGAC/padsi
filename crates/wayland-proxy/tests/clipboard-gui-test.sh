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
zone1_mode="wayland"

zone2_name="UNSECURE"
zone2_mode="wayland"
#zone2_mode="x11"

script_dir=$(realpath "$0")
script_dir=$(dirname "$script_dir")

echo "Building debug version"
cargo build || exit 1

# cleanups from previous run
rm -rf logs-*

# starting Wayland proxies
echo "Starting ${zone1_name} proxy"
mkdir -p "$script_dir/logs-${zone1_name}"
LOG_DIR="$script_dir/logs-${zone1_name}" RUST_BACKTRACE=1 cargo run -- /run/user/1000/wayland-0 "$script_dir/wayland-proxy-${zone1_name}.sock" "${zone1_name}" "${zone2_name}" &
zone1_proxy_pid=$!
echo "${zone1_name} proxy's PID: $zone1_proxy_pid"

echo "Starting ${zone2_name} proxy"
mkdir -p "$script_dir/logs-${zone2_name}"
LOG_DIR="$script_dir/logs-${zone2_name}" RUST_BACKTRACE=1 cargo run -- /run/user/1000/wayland-0 "$script_dir/wayland-proxy-${zone2_name}.sock" "${zone2_name}" &
zone2_proxy_pid=$!
echo "${zone2_name} proxy's PID: $zone2_proxy_pid"

sleep 1

C_DISPLAY=$DISPLAY

# starting zone1 client
echo "Starting $zone1_name client"
case $zone1_mode in
    x11)
        unset WAYLAND_DISPLAY
        unset XDG_RUNTIME_DIR
        ;;
    wayland)
        unset DISPLAY
        export WAYLAND_DISPLAY="$script_dir/wayland-proxy-${zone1_name}.sock"
        ;;
    *)
        echo "Unknown mode '$zone1_mode'"
        exit 1
esac


$script_dir/test-ui.py "${zone1_name}" >/dev/null &
zone1_client_pid=$!


# starting zone2 client
echo "Starting $zone2_name client"
case $zone2_mode in
    x11)
        unset WAYLAND_DISPLAY
        unset XDG_RUNTIME_DIR
        export DISPLAY=$C_DISPLAY
        ;;
    wayland)
        unset DISPLAY
        export WAYLAND_DISPLAY="$script_dir/wayland-proxy-${zone2_name}.sock"
        ;;
    *)
        echo "Unknown mode '$zone2_mode'"
        exit 1
esac

$script_dir/test-ui.py "${zone2_name}" >/dev/null &
zone2_client_pid=$!

trap ctrl_c INT

function ctrl_c() {
    echo "Exiting..."
    kill $zone1_client_pid
    kill $zone2_client_pid
    kill $zone1_proxy_pid
    kill $zone2_proxy_pid
    rm *.sock
    exit 0
}

echo -n "PRESS CTRL-C"
while true; do sleep 3600; done
