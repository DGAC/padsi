#!/usr/bin/env python3

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

import argparse
import array
import json
import os
import socket
import sys
import syslog
from typing import Any

# fusermount's accepted arguments from the man page
parser=argparse.ArgumentParser()
parser.add_argument("mountpoint", type=str)
parser.add_argument("-V", action="store_true")
parser.add_argument("-o", type=str)
parser.add_argument("-u", action="store_true")
parser.add_argument("-q", action="store_true")
parser.add_argument("-z", action="store_true")
args=parser.parse_args()

socket_path="/bubble/run/padsi-fuse.sock"

def send_fd(sock:socket.socket, fd:int|None, data:Any):
    """Send JSON encoded data and a single file descriptor if specified"""
    if fd is not None:
        fds=array.array("i", [fd])
        sock.sendmsg(
            [json.dumps(data).encode()],
            [(socket.SOL_SOCKET, socket.SCM_RIGHTS, fds)]
        )
    else:
        sock.sendmsg([json.dumps(data).encode()])

def main():
    # get FD to pass to server
    fuse_fd:int|None=None
    fuse_fd_str=os.environ.get("_FUSE_COMMFD")
    if fuse_fd_str:
        try:
            fuse_fd=int(fuse_fd_str)
        except ValueError:
            print("Invalid _FUSE_COMMFD value", file=sys.stderr)
            sys.exit(1)

    # args. to send to server
    mp=args.mountpoint if os.path.isabs(args.mountpoint) else os.path.join(os.getcwd(), args.mountpoint)
    data={
        "prog": "fusermount",
        "mp": mp,
        "V": args.V,
        "o": args.o,
        "u": args.u,
        "q": args.q,
        "z": args.z
    }

    # Connect to daemon and send fd and server data
    sock=socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect(socket_path)
    send_fd(sock, fuse_fd, data)

    # wait for a response
    response=sock.recv(4096)
    try:
        resp_json=json.loads(response.decode())
        print(resp_json.get("stdout", ""), end="")
        print(resp_json.get("stderr", ""), end="", file=sys.stderr)
        sys.exit(resp_json.get("returncode", 1))
    except Exception as e:
        syslog.syslog(syslog.LOG_ERR, f"Unexpected response from mount-server: '{response.decode()}' ({str(e)})")
        sys.exit(1)

if __name__ == "__main__":
    main()
