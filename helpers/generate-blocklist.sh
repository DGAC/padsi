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
# download an updated version of the blocked lst
#

blocklist_file="blocklist.conf"

echo "server:" > "$blocklist_file"
curl -s https://raw.githubusercontent.com/StevenBlack/hosts/master/alternates/fakenews-gambling-porn/hosts | \
        grep ^0.0.0.0 - | \
        sed 's/ #.*$//;
        s/^0.0.0.0 \(.*\)/  local-zone: "\1" refuse/' \
        >> "$blocklist_file"
