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

[ "$PADSI_ETC_DIR" == "" ] && {
    echo "codebug: PADSI_ETC_DIR is undefined" >&2
    exit 1
}

[ "$PADSI_LIB_DIR" == "" ] && {
    echo "codebug: PADSI_LIB_DIR is undefined" >&2
    exit 1
}

scriptdir="$PADSI_LIB_DIR"
source "$PADSI_ETC_DIR/config.sh"

[ "$PADSI_VM_USAGE" == "RUN" ] || {
    logger -p user.err "$0 script called but VM usage is '$PADSI_VM_USAGE'"
    exit 1
}

# configure network interface
logger -p user.info "Configuring network interface"
"$scriptdir/dhcp.sh"

# define hostname
logger -p user.info "Defining host name"
[ "$PADSI_VM_NICKNAME" == "" ] || {
    host=$(echo "$PADSI_VM_NICKNAME" | sed -e 's/_/-/g')
    [ "$(hostname)" == "$host.vm" ] || {
        hostname "$host.vm"
    }
}

# copy SSH keys if present
"$scriptdir/authorize-ssh-key.sh"
