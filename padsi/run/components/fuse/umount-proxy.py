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

import json
import os
import socket
import sys

socket_path="/bubble/run/padsi-fuse.sock"

def main():
    # args. to send to server
    if len(sys.argv)==0:
        raise Exception("Invalid invocation")
    mp=None
    if len(sys.argv)>1:
        mp=sys.argv[-1]
        if not os.path.isabs(mp):
            mp=os.path.join(os.getcwd(), mp)
        if not os.path.exists(mp):
            mp=None
        else:
            sys.argv.pop()
    data={
        "prog": "umount",
        "mp": mp,
        "args": sys.argv[1:]
    }

    # Connect to daemon and send fd and server data
    sock=socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect(socket_path)
    sock.sendmsg([json.dumps(data).encode()])

    # wait for a response
    response=sock.recv(4096)
    try:
        resp_json=json.loads(response.decode())
        print(resp_json.get("stdout", ""), end="")
        print(resp_json.get("stderr", ""), end="", file=sys.stderr)
        sys.exit(resp_json.get("returncode", 1))
    except Exception:
        print("Unexpected response from daemon:", response.decode(), file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
