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
scriptdir="$PADSI_LIB_DIR"
source "$PADSI_ETC_DIR/config.sh"

[ "$PADSI_VM_USAGE" == "UPDATE" ] || {
    logger -p user.err "$0 script called but VM usage is '$PADSI_VM_USAGE'"
    exit 1
}

# configure network interface
"$scriptdir/dhcp.sh"

logger -p user.info "Starting system update"

# check all the expected env. variables are present
logfile="/var/log/padsi-updates.log"

# perform the update
now=$(date -u "+%Y-%m-%d %H:%M:%S")
echo "update started @$now" >> "$logfile"
apt -y update 2>&1 >> "$logfile"
apt -y upgrade 2>&1 >> "$logfile"
apt -y autoremove 2>&1 >> "$logfile"
now=$(date -u "+%Y-%m-%d %H:%M:%S")
echo "update finished @$now" >> "$logfile"
echo "" >> "$logfile"

logger -p user.info "Finished system update"
