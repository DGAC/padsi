#!/usr/bin/python3

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

import array
import socket
import sys

# This program does not accept any argument, but upon a connection from a client, it receives the "<bus num> <dev num>\n" string
# specifying which device the client wants to work on.
#
# Note: the location of the spice-client-glib-usb-acl-helper program the Spice client uses can also be specified using
# the SPICE_USB_ACL_BINARY env. variable.
#
# refer to spice-gtk's source code:
# - src/spice-client-glib-usb-acl-helper.c
# - src/usb-acl-helper.c

socket_path="/bubble/run/padsi-usbredir.sock"

def send_fds(sock: socket.socket, payload: bytes, fds):
    fds_arr=array.array("i", fds)
    ancdata=[(socket.SOL_SOCKET, socket.SCM_RIGHTS, fds_arr)]
    # send at least one byte of payload with the control message
    sock.sendmsg([payload], ancdata)

def main():
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.connect(socket_path)
        send_fds(s, b"X", [sys.stdin.fileno(), sys.stdout.fileno(), sys.stderr.fileno()])

        try:
            resp=s.recv(1024)
            if resp:
                status=int(resp.decode())
                sys.exit(status)
            else:
                sys.exit(127)
        except Exception as e:
            print(f".... {str(e)}")
            sys.exit(128)

if __name__ == "__main__":
    main()
