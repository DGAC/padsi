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
import asyncio
import os
import socket
import sys
import syslog
from typing import List, Tuple


def recv_fds_once(sock: socket.socket, msglen: int, nfds: int) -> Tuple[bytes, List[int]]:
    """
    Receive exactly `nfds` file descriptors via SCM_RIGHTS on a Unix domain socket.
    Returns (payload_bytes, fds).
    """
    fds=array.array("i")  # int array for FDs
    ancbuf=socket.CMSG_SPACE(nfds * fds.itemsize)
    (msg, ancdata, _flags, _)=sock.recvmsg(msglen, ancbuf)
    for (cmsg_level, cmsg_type, cmsg_data) in ancdata:
        if cmsg_level==socket.SOL_SOCKET and cmsg_type==socket.SCM_RIGHTS:
            # trim to a multiple of itemsize and extend
            take=len(cmsg_data) - (len(cmsg_data) % fds.itemsize)
            fds.frombytes(cmsg_data[:take])
    return (msg, list(fds))

async def handle_client(_reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    raw_transport_sock=writer.get_extra_info("socket")
    sock_fd=raw_transport_sock.fileno() # real OS-level socket
    real_sock=socket.socket(fileno=sock_fd)  # now it's a normal socket.socket
    #real_sock.setblocking(True) # don't!

    fds=None
    try:
        # expect 3 FDs: stdin, stdout, stderr
        (_, fds)=recv_fds_once(real_sock, msglen=1, nfds=3)
        if len(fds)!=3:
            try:
                syslog.syslog(syslog.LOG_ERR, f"expected 3 FDs, got {len(fds)}")
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()
            return

        # set FDs to be inheritable so the child has them
        for fd in fds:
            os.set_inheritable(fd, True)

        # remap FDs to 0/1/2 in the child before exec()
        def _preexec():
            # Dup the received FDs onto standard streams.
            os.dup2(fds[0], 0)  # stdin
            os.dup2(fds[1], 1)  # stdout
            os.dup2(fds[2], 2)  # stderr

            # close the originals in the child (don't need them anymore)
            if fds[0] > 2:
                os.close(fds[0])
            if fds[1] > 2:
                os.close(fds[1])
            if fds[2] > 2:
                os.close(fds[2])

        # spawn the subprocess
        args=["/usr/libexec/spice-client-glib-usb-acl-helper"]
        proc = await asyncio.create_subprocess_exec(
            *args, stdin=None, stdout=None, stderr=None,
            pass_fds=fds, # ensure these FDs are preserved across exec
            preexec_fn=_preexec # dup2 before exec in the child
        )

        # wait for the process to end.
        returncode=await proc.wait()

        # send status back to the peer
        try:
            writer.write(f"{returncode}".encode())
            await writer.drain()
        except Exception:
            pass

    finally:
        # Close the received FDs in the parent (server) after spawning.
        # (Child either inherited or duped them already.)
        if fds is not None:
            try:
                for fd in fds:
                    try:
                        os.close(fd)
                    except Exception:
                        pass
            except Exception:
                pass

        writer.close()
        await writer.wait_closed()

async def main(socket_path:str):
    # Remove any stale socket file
    try:
        os.unlink(socket_path)
    except FileNotFoundError:
        pass

    server=await asyncio.start_unix_server(handle_client, path=socket_path)
    os.chmod(socket_path, 0o666)
    async with server:
        await server.serve_forever()

if __name__ == "__main__":
    try:
        # parse command line arguments
        if len(sys.argv)!=2:
            raise Exception(f"Usage: {__file__} <socket path>")
        asyncio.run(main(sys.argv[1]))
    except KeyboardInterrupt:
        pass
