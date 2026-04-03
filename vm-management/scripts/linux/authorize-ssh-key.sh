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

logger -p user.info "Copying SSH keys if present"
homedir=$(getent passwd "$PADSI_USER_NAME" | cut -d: -f6)
sshdir="$homedir/.ssh"

rm -f "$sshdir/authorized_keys"

function add_key() {
    local keyfile="$1"
    [ -d "$sshdir" ] || {
        mkdir "$sshdir"
        chmod 700 "$sshdir"
    }
    logger -p user.info "Adding SSH key '$keyfile'"
    cat "$keyfile" >> "$sshdir/authorized_keys"
}

[ -d "$PADSI_USER_DIR/.ssh" ] && {
    for file in "$PADSI_USER_DIR/.ssh"/*
    do
        ext="${file##*.}"
        [ "$ext" == "pub" ] && {
            add_key "$file"
        }
    done
}

[ -f "$PADSI_ETC_DIR/padsi-vm-key.pub" ] && {
    add_key "$PADSI_ETC_DIR/padsi-vm-key.pub"
}

# set correct owner
[ -d "$sshdir" ] && {
    chown -R "$PADSI_USER_ID:$PADSI_GROUP_ID" "$sshdir"
}

exit 0