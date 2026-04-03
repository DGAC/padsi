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

import asyncio
import json
import logging
import os
import random
import socket
import struct
import syslog

from .buffer import ConnectionClosed
from .client import DBusClient, Selector


class DBusRouter:
    """D-Bus server which receives incoming connections from actual D-Bus clients
    """
    def __init__(self, socket_path:str, default_dbus_socket:str):
        self._socket_path=socket_path
        self._guid=random.randbytes(16).hex().encode()

        self._selectors:list[Selector]=[]
        self._default_dbus_socket=default_dbus_socket
        self._clients:dict[asyncio.Task,DBusClient]={}

        self._connection_name_allocating_server:str|None=None

    def set_connection_name_allocating_server(self, server_socket_path:str):
        """Tell the router to first connect to the specified D-Bus server so
        it gets its connection name from it
        """
        self._connection_name_allocating_server=server_socket_path

    def add_selector(self, selector:Selector):
        """Add a new selector to specify where messages are routed to
        """
        self._selectors.append(selector)

    async def _authenticate_client(self, client:DBusClient):
        """Authenticate a client
        """
        try:
            line=await client.readline()
            if line is None or len(line)==0 or line[0]!=0:
                raise Exception(f"Invalid AUTH from client '{line}'")

            try:
                line=line[1:].strip().decode() # remove the leading "\0"
            except Exception:
                await client.write(b"ERROR not ASCII\r\n")
                raise Exception(f"Data is not ASCII '{line}'")

            if not line.startswith("AUTH"):
                await client.write(b"ERROR no AUTH\r\n")
                raise Exception(f"Invalid AUTH from client '{line}'")

            if line=="AUTH":
                # tell the client which authn. protocols are supported
                await client.write(b"REJECTED EXTERNAL\r\n")
                line=await client.readline()
                try:
                    line=line.strip().decode()
                except Exception:
                    await client.write(b"ERROR not ASCII\r\n")
                    raise Exception(f"Data is not ASCII '{line}'")

            if not line.startswith("AUTH EXTERNAL"):
                await client.write(b"ERROR AUTH not supported\r\n")
                raise Exception(f"AUTH method not supported '{line}'")

            init_resp=line[14:]
            if init_resp=="":
                await client.write(b"DATA\r\n") # TODO
                line=await client.readline()
                line=line.strip()

            # Accept it (you could check the UID hex here if you want)
            await client.write(b"OK "+self._guid+b"\r\n")

            line=await client.readline()
            line=line.strip()
            if line==b"NEGOTIATE_UNIX_FD":
                await client.write(b"AGREE_UNIX_FD\r\n")
                line=await client.readline()
                line=line.strip()

            if not line or not line.strip() == b'BEGIN':
                raise Exception(f"Expected 'BEGIN', got '{line}'")
            return True
        except ConnectionClosed as e:
            raise e
        except Exception as e:
            logging.error(f"Authentication error: {str(e)}")
            raise e

    def _client_disconnected_cb(self, task:asyncio.Task):
        client=self._clients[task]
        if client is None:
            logging.error("CODEBUG: self._clients[task] is None")
        else:
            client.connected=False
            if task.exception() is not None:
                txt=f"Client {client.dbus_name} disconnected: {task.exception()}"
                syslog.syslog(syslog.LOG_ERR, txt)
                logging.error(json.dumps({
                    "context": "client-disconnected",
                    "client": client.id,
                    "extra": txt
                }))
            else:
                logging.info(json.dumps({
                    "context": "client-disconnected",
                    "client": client.id,
                    "extra": f"Client {client.dbus_name} disconnected!"
                }))
        del self._clients[task]

    async def _new_client(self, client_sock:socket.socket):
        """Handle a new client which has just connected to our service
        """
        ucred=client_sock.getsockopt(socket.SOL_SOCKET, 17, struct.calcsize('3i')) # 17 for SO_PEERCRED
        (pid, uid, gid)=struct.unpack('3i', ucred)
        client=DBusClient(self._selectors, self._default_dbus_socket, client_sock)
        logging.info(json.dumps({
            "context": "client-connected",
            "client": client.id,
            "extra": f"Client connected: {client_sock} for {pid, uid, gid}"
        }))


        try:
            # Perform D-Bus AUTH EXTERNAL handshake first
            try:
                await self._authenticate_client(client)
            except ConnectionClosed:
                logging.info(json.dumps({
                    "context": "client-disconnected",
                    "client": client.id,
                    "extra": f"Client {client_sock} disconnected during authn."
                }))
                return
            except Exception as e:
                client_sock.close()
                txt=f"Client authentication failed: {str(e)}"
                syslog.syslog(syslog.LOG_ERR, txt)
                logging.error(json.dumps({
                    "client": client.id,
                    "context": "client-disconnected",
                    "extra": txt
                }))
                return

            # open connection to prefered D-Bus server before handling client's messages
            if self._connection_name_allocating_server is not None:
                await client.set_connection_name_allocating_server(self._connection_name_allocating_server)

            # Create task for client handling incoming D-Bus messages
            task=asyncio.create_task(client.run())
            self._clients[task]=client
            task.add_done_callback(self._client_disconnected_cb)

        except Exception as e:
            txt=f"Client disconnected or error: {e}"
            syslog.syslog(syslog.LOG_ERR, txt)
            logging.error(json.dumps({
                "context": "client-disconnected",
                "extra": txt
            }))

    async def run(self):
        if os.path.exists(self._socket_path):
            os.unlink(self._socket_path)

        server_sock=socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server_sock.bind(self._socket_path)
        server_sock.listen(1)
        server_sock.setblocking(False)
        logging.info(json.dumps({
            "context": "info",
            "extra": f"D-Bus Proxy Router running on {self._socket_path}"
        }))

        loop = asyncio.get_running_loop()
        while True:
            try:
                (client_sock, _)=await loop.sock_accept(server_sock)
                await self._new_client(client_sock)
            except asyncio.exceptions.CancelledError:
                logging.info(json.dumps({
                    "context": "info",
                    "extra": "Cancelled, exiting"
                }))
                return
