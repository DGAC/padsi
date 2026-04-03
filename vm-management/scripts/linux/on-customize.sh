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
source "$PADSI_ETC_DIR/config.sh"

[ "$PADSI_VM_USAGE" == "CUSTOMIZE" ] || {
    logger -p user.err "$0 script called but VM usage is '$PADSI_VM_USAGE'"
    exit 1
}

logger -p user.info "Starting user customization"

#
# add group for user
#
getent group "$PADSI_GROUP_ID" || {
    logger -p user.info "Creating group $PADSI_GROUP_NAME ($PADSI_GROUP_ID)"
    groupadd -g "$PADSI_GROUP_ID" "$PADSI_GROUP_NAME"
}

#
# add user
#
userdata=$(getent passwd "$PADSI_USER_ID") || {
    logger -p user.info "Creating user $PADSI_USER_NAME ($PADSI_USER_ID)"
    useradd -c "$PADSI_USER_FULLNAME" -g "$PADSI_GROUP_ID" -m -u "$PADSI_USER_ID" -s "$PADSI_USER_SHELL" "$PADSI_USER_NAME"
    false
} && {
    logger -p user.info "Updating user $PADSI_USER_NAME ($PADSI_USER_ID)"
    username=$(cut -d':' -f1 <<<"$userdata" | sed -e s'/,//g')
    usermod -s "$PADSI_USER_SHELL" --login "$PADSI_USER_NAME" -c "$PADSI_USER_FULLNAME" -a -G "$PADSI_GROUP_ID" "$username"
}

# set empty password
passwd -d "$PADSI_USER_NAME"

#
# set autologin
#
[ -f "/etc/gdm3/daemon.conf" ] && {
    # enabling GDM automatic login, must have:
    # AutomaticLoginEnable = true
    # AutomaticLogin = <username>
    sed -e 's/^.*AutomaticLoginEnable.*/AutomaticLoginEnable = true/' -e "s/^.*AutomaticLogin[^E].*/AutomaticLogin = $PADSI_USER_NAME/" -i /etc/gdm3/daemon.conf
    true
} || {
    [ -f "/lib/systemd/system/getty@.service" ] && {
        # enabling console autologin
        sed -e "s/--noclear/--noclear -a $PADSI_USER_NAME/" -i /lib/systemd/system/getty@.service
    }
}

#
# set up profile
#
cat <<EOF >> /etc/profile
if [ -f /run/padsi-agent/etc/config.sh ]; then
    source /run/padsi-agent/etc/config.sh
fi
EOF

#
# hide GNOME initial setup
#
homedir=$(getent passwd "$PADSI_USER_NAME" | cut -d: -f6)
mkdir -p "$homedir/.config"
echo -n "yes" > "$homedir/.config/gnome-initial-setup-done"

# set correct ownership
chown -R $PADSI_USER_ID:$PADSI_GROUP_ID "$homedir"

# shutdown the system
logger -p user.info "Finished user customization, shutting down"
poweroff
