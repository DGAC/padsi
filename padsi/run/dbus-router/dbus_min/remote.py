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

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import syslog
from typing import Any, Callable, Optional, Union

from .buffer import ConnectionClosed, SocketBuffer
from .message import Message, MessageHeaderType


class DBusRemoteService:
    """Represent a connection established to another D-Bus server (where this object is thus a D-Bus client)
    """
    def __init__(self, socket_path:str):
        self._socket_path=socket_path
        self._messages_handlers:set[Callable]=set()
        self._reply_queues:dict[int,asyncio.Queue]={}
        self._in_queue=asyncio.Queue()
        self._guid:str|None=None
        self._messages_handling_tasks:set[asyncio.Task]=set()

        self._id=self._socket_path

        self._lock=asyncio.Lock()
        self._sock:socket.socket|None=None
        self._buffer:SocketBuffer|None=None
        self._client_dbus_name:str|None=None # D-Bus name as which the client knows itself
        self._assigned_dbus_name:str|None=None # unique connection name assigned by the D-Bus server

        self._private_hello=0 # set to >0 if the Hello() call and subsequent NameAcquired signal
                              # are not asked by the real D-Bus client (i.e. when the D-Bus router
                              # opens a connection to a new remote D-Bus server)

    async def _readline(self) -> bytes:
        """Read a line"""
        if self._buffer is None:
            raise Exception("CODEBUG: self._buffer should not be None")
        return await self._buffer.readline()

    async def _write(self, data:bytes, ancillary_data:Any=None):
        if self._buffer is None:
            raise Exception("CODEBUG: self._buffer should not be None")
        await self._buffer.write(data, ancillary_data)

    async def connect(self):
        """Actually open the connection
        """
        async with self._lock:
            if self._sock is not None:
                return

            loop=asyncio.get_running_loop()
            logging.info(json.dumps({
                "context": "remote-connect",
                "to": self._socket_path
            }))

            if not os.path.exists(self._socket_path):
                raise Exception(f"Unix socket '{self._socket_path}' does not exist")
            client_sock=socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client_sock.setblocking(False)
            await loop.sock_connect(client_sock, self._socket_path)

            self._sock=client_sock
            self._buffer=SocketBuffer(self._sock, self._id)

            await self._authenticate()

    @property
    def socket_path(self) -> str:
        return self._socket_path

    @property
    def guid(self) -> str|None:
        return self._guid

    @property
    def client_dbus_name(self) -> str|None:
        """Unique name assigned by the 1st D-Bus server the client connected to
        (as a reply to the Hello call)
        """
        return self._client_dbus_name

    @client_dbus_name.setter
    def client_dbus_name(self, name:str):
        self._client_dbus_name=name

    @property
    def assigned_dbus_name(self) -> str|None:
        """Unique name assigned by the D-Bus server this object is connected to
        (as a reply to the Hello call)
        """
        return self._assigned_dbus_name

    @assigned_dbus_name.setter
    def assigned_dbus_name(self, name:str):
        self._assigned_dbus_name=name

    def add_message_handler(self, handler:Callable[[Message, DBusRemoteService], Optional[Union[Message, bool]]]):
        """Add a custom message handler for incoming METHOD_CALL and SIGNAL messages.

        The handler should be an async callable that takes a Message object and self as argument

        If the message is a method call, the callable may return
        another Message as a reply and it will be marked as handled. The handler
        also return True to mark the message as handled without reply.

        Notes:
        - If multiple message handlers are registered, and if the caller expects a reply, the reply sent is the first reply a
          handler provides.
        - the message passed to each handler is AS-IS, without any address translation
        """
        if not callable(handler):
            raise TypeError("The message handler must be callable with a single parameter")
        self._messages_handlers.add(handler)

    def translate_to_client(self, msg:Message) -> Message:
        """Change the DESTINATION of a message if necessary to match what a client expects
        """
        if self.assigned_dbus_name is None:
            # nothing to do
            return msg

        if msg.destination is not None and msg.destination[0]==":" and msg.destination!=self.client_dbus_name:
            transl={MessageHeaderType.DESTINATION: self.client_dbus_name}
            logging.debug(json.dumps({
                "context": "transl-to-client",
                "extra": f"{msg.destination} -> {self.client_dbus_name}",
                "msg-serial": msg.serial
            }))
            return msg.change_header(transl)
        return msg

    def translate_to_remote(self, msg:Message) -> Message:
        """Change the SENDER of a message if necessary to match what a remote D-Bus expects
        """
        if self.assigned_dbus_name is None:
            # nothing to do
            return msg
        if msg.sender is not None and msg.sender[0]==":" and msg.sender!=self.assigned_dbus_name:
            transl={MessageHeaderType.SENDER: self.assigned_dbus_name}
            logging.debug(json.dumps({
                "context": "transl-to-remote",
                "extra": f"{msg.sender} -> {self.assigned_dbus_name}",
                "msg-serial": msg.serial
            }))
            return msg.change_header(transl)
        return msg

    async def forward_message(self, msg:Message):
        await self._write(msg.blob, msg.ancillary_data)

    async def _handle_remote_message(self, msg:Message):
        """Handle a message which has come from the remote D-Bus server
        """
        # pass the message to the client object which does all the work
        for func in self._messages_handlers:
            try:
                await func(msg, self)
            except Exception as e:
                txt=f"Client failed to handle remote message {msg}: {str(e)}"
                syslog.syslog(syslog.LOG_WARNING, txt)
                logging.warning(json.dumps({
                    "text": txt
                }))

    async def _handle_all_incoming_messages(self):
        msg:Message|None=None
        while True:
            msg=await self._in_queue.get()
            if msg is None:
                return
            try:
                await self._handle_remote_message(msg)
            except Exception as e:
                txt=f"Failed to handle D-Bus remote message {msg}: {str(e)}"
                syslog.syslog(syslog.LOG_WARNING, txt)
                logging.warning(json.dumps({
                    "text": txt
                }))

    async def run(self):
        """Process all the messages coming from the remote D-Bus server
        """
        asyncio.create_task(self._handle_all_incoming_messages())

        if self._buffer is None:
            raise Exception("CODEBUG: self._buffer should not be None")
        try:
            if self._buffer.length==0:
                await self._buffer.read()
            while True:
                while True:
                    msg=self._buffer.get_dbus_message()
                    if msg is not None:
                        await self._in_queue.put(msg)
                    else:
                        break
                await self._buffer.read()
        except ConnectionClosed:
            pass
        finally:
            self._in_queue.put_nowait(None)
            if self._sock is not None:
                self._sock.close()
                self._sock=None

    async def _authenticate(self):
        """Authenticate to the server
        """
        await self._write(b"\x00AUTH\r\n")
        line=await self._readline()
        if line==b"REJECTED EXTERNAL\r\n":
            await self._write(b"AUTH EXTERNAL\r\n")
            line=await self._readline()
        if line==b"DATA\r\n":
            await self._write(b"DATA\r\n")
            line=await self._readline()
        if line.startswith(b"OK "):
            (_, guid)=line.split(b" ")
            guid=guid.strip().decode()
            self._guid=guid

            await self._write(b"NEGOTIATE_UNIX_FD\r\n")
            line=await self._readline()
            if not line.startswith(b"AGREE_UNIX_FD"):
                logging.warning("Remote did not Aggree to pass Unix FD")

            await self._write(b"BEGIN\r\n")
            return

        raise Exception(f"Failed to authenticate to remote D-Bus server (last received: {line})")
