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
import base64
import enum
import json
import logging
import socket
import syslog
from dataclasses import dataclass, field
from typing import Any, Callable

from .buffer import ConnectionClosed, SocketBuffer
from .message import Message, MessageType
from .remote import DBusRemoteService


class Selector:
    """Represent a selector for D-Bus messages
    """
    def __init__(self, socket_path:str, service:str|None=None, object_path:str|None=None, interfaces:list[str]|None=None):
        self._socket_path=socket_path
        self._service=service
        self._object_path=object_path
        self._interfaces=interfaces if interfaces is not None else []

    @property
    def socket_path(self) -> str:
        return self._socket_path

    def match_service(self, name:str) -> bool:
        return False if self._service is None else name==self._service

    def match_object_path(self, path:str) -> bool:
        return False if self._object_path is None else path.startswith(self._object_path)

    def match_interface(self, iface:str) -> bool:
        return iface in self._interfaces or iface.startswith("org.freedesktop.DBus.")

class NoticeableCall(str,enum.Enum):
    Hello="Hello"
    GetNameOwner="GetNameOwner"
    AddMatch="AddMatch"
    RemoveMatch="RemoveMatch"
    StartServiceByName="StartServiceByName"
    CreateSession="CreateSession"

@dataclass
class RemoteContext:
    """Context around a remote D-Bus service
    """
    remote:DBusRemoteService # remote D-Bus server
    run_task:asyncio.Task # task to handle all incoming messages of the remote D-Bus server
    name_owners:dict[str,str]=field(init=False) # key=service name owner (like :34.0), value=owned service name
    objects:set[str]=field(init=False) # list of objects paths associated with that context
    hello_queue:asyncio.Queue|None=None # used to pass Hello() reply when saying Hello() to a new remote D-Bus server

    def __post_init__(self):
        self.name_owners={}
        self.objects=set()

@dataclass
class MessageContext:
    """Data to keep track of message call and reply to perform analysis or routing
    """
    message:Message # METHOD_CALL message being routed
    context:RemoteContext # the remote context
    callback:Callable[[Message, Message, RemoteContext], None]|None=None # callback function when the reply (METHOD_RETURN or ERROR) is received

class DBusClient:
    """Represent a client application which has opened a connection to our service
    """
    client_serial:int=0

    def __init__(self, selectors:list[Selector], default_dbus_socket:str, sock:socket.socket, connection_name_allocating_server:str|None=None):
        self._selectors=selectors
        self._default_dbus_socket=default_dbus_socket
        self._sock=sock

        self._client_serial=DBusClient.client_serial
        DBusClient.client_serial+=1
        self._id=f"CLIENT-{self._client_serial}"

        self._in_queue=asyncio.Queue() # queue of messages received from the client

        self._buffer=SocketBuffer(self._sock, self._id)

        self._dbus_name:str|None=None # unique D-Bus name provided by the 1st remote D-Bus server we contacted
                                 # the D-Bus client only sees that name

        self._lock=asyncio.Lock()
        self._remotes:dict[str,RemoteContext]={} # key=socket path
        self._messages_contexts_table:dict[int,MessageContext]={} # key=serial of a METHOD_CALL message received from the client
        self._routing_table:dict[int,MessageContext]={} # key=serial of a METHOD_CALL message received from a remote

        self._connection_name_allocating_server:str|None=None
        self._hello_message:Message|None=None # Hello message captured from the 1st one sent

    async def set_connection_name_allocating_server(self, server_socket_path:str):
        """Define the D-Bus remote server which has to be the one where the first Hello() message
        is sent, so it's also the one assigning the D-Bus connection name of the client
        """
        self._connection_name_allocating_server=server_socket_path

    async def readline(self) -> bytes:
        if self.connected:
            return await self._buffer.readline()
        return b""

    async def write(self, data:bytes, ancillary_data:Any|None=None):
        if self.connected:
            await self._buffer.write(data, ancillary_data)

    @property
    def dbus_name(self) -> str|None:
        return self._dbus_name

    @property
    def id(self) -> str:
        return self._id

    @property
    def connected(self) -> bool:
        return self._sock is not None

    @connected.setter
    def connected(self, connected:bool):
        if connected:
            raise Exception("Connection is already closed, can't reopen it")
        if self._sock is not None:
            self._sock.close()
            self._sock=None


    async def _connect_to_remote(self, socket_path:str) -> RemoteContext:
        """Open a connection to a remote D-Bus service
        """
        svce=DBusRemoteService(socket_path)
        await svce.connect()
        svce.add_message_handler(self._handle_remote_message) # pyright: ignore
        task=asyncio.create_task(svce.run())
        context=RemoteContext(svce, task)
        self._remotes[socket_path]=context
        return context

    def _selectors_match_service(self, name:str) -> str|None:
        for selector in self._selectors:
            if selector.match_service(name):
                return selector.socket_path
        return None

    def _selectors_match_object_path(self, path:str) -> str|None:
        for selector in self._selectors:
            if selector.match_object_path(path):
                return selector.socket_path
        return None

    def _selectors_match_interface(self, iface:str) -> str|None:
        for selector in self._selectors:
            if selector.match_interface(iface):
                return selector.socket_path
        return None

    async def _get_remote_to_route_to(self, msg:Message) -> DBusRemoteService:
        """Determine socket path of the D-Bus service to route the message to using selectors and
        the context associated with all the remote D-Bus services.

        Note: it always falls back to the default remote D-Bus server.
        """
        socket_path:str|None=None
        reason:str|None=None
        ncall=self._identify_noticeable_call(msg)

        if len(self._remotes)==0 and self._connection_name_allocating_server is not None:
            socket_path=self._connection_name_allocating_server
            if socket_path is not None:
                reason="connection name allocating server"

        # use the destination service name
        if socket_path is None and msg.destination is not None:
            socket_path=self._selectors_match_service(msg.destination)
            if socket_path is not None:
                reason="matching message.destination"

        # check the object path manipulated by some calls
        opath:str|None=None
        if socket_path is None:
            if ncall in (NoticeableCall.AddMatch, NoticeableCall.RemoveMatch):
                try:
                    rule=msg.get_call_rule()
                    if rule is not None:
                        #logging.debug(f"msg #{msg.serial} from {msg.from_descr} {ncall} RULE: {rule}")
                        name=rule.get("arg0")
                        if name is not None:
                            socket_path=self._selectors_match_interface(name)
                            if socket_path is not None:
                                reason=f"interface match ('{name}' from message rule)"
                            if socket_path is None:
                                socket_path=self._selectors_match_service(name)
                                if socket_path is not None:
                                    reason=f"service match ('{name}' from message rule)"
                        if socket_path is None:
                            opath=rule.get("path")
                            if rule.get("type")=="signal":
                                sender=rule.get("sender")
                                if sender is not None:
                                    socket_path=self._selectors_match_service(sender)
                                    if socket_path is not None:
                                        reason=f"service match ('{sender}' from message sender)"
                except Exception as e:
                    txt=f"Could not parse add/remove match rule: {str(e)}"
                    syslog.syslog(syslog.LOG_WARNING, txt)
                    logging.warning(json.dumps({
                        "text": txt
                    }))
            elif ncall==NoticeableCall.StartServiceByName:
                name=msg.unmarshall_body("su")
                socket_path=self._selectors_match_service(name)
                if socket_path is not None:
                    reason=f"service match ('{name}' from StartServiceByName)"

        # check the object path if we identified one
        if socket_path is None:
            if opath is not None:
                socket_path=self._selectors_match_object_path(opath)
                if socket_path is not None:
                    reason=f"object path match ('{opath}' from message's path)"
                if socket_path is None:
                    for (sp, context) in self._remotes.items():
                        if opath in context.objects:
                            socket_path=sp
                            reason=f"object path match ('{opath}' from discovered object's path)"
                            break

        # use owned name services as D-Bus messages' destination will often be the unique name of the
        # current service (like ":1.34" and not the name of the service we can filter on like
        # "org.freedesktop.portal.Desktop")
        if socket_path is None and msg.destination is not None:
            for (_, context) in self._remotes.items():
                name=context.name_owners.get(msg.destination)
                if name is not None:
                    socket_path=self._selectors_match_service(name)
                    if socket_path is not None:
                        reason=f"service match ('{name}' from message's destination)"
                        break

        # if we query a service name which will match, then use the correct D-Bus remote
        if socket_path is None:
            if ncall==NoticeableCall.GetNameOwner:
                name=msg.unmarshall_body("s")
                socket_path=self._selectors_match_service(name)
                if socket_path is not None:
                    reason=f"service match ('{name}' from GetNameOwner)"

        # check that the queried interface is allowed
        if socket_path is not None and ncall!=NoticeableCall.Hello:
            if msg.interface is not None and msg.interface!="org.freedesktop.DBus":
                sp2=self._selectors_match_interface(msg.interface)
                if sp2!=socket_path:
                    logging.debug(json.dumps({
                        "context": "not-routing",
                        "extra": f"Interface {msg.interface} not allowed but {reason}",
                        "to": socket_path,
                        "msg-from": self._id,
                        "msg-serial": msg.serial
                    }))
                    socket_path=None

        if socket_path is not None:
            logging.debug(json.dumps({
                "context": "routing",
                "extra": reason,
                "to": socket_path,
                "msg-from": self._id,
                "msg-serial": msg.serial
            }))
        else:
            # fall back to default D-Bus remote
            socket_path=self._default_dbus_socket
            logging.debug(json.dumps({
                "context": "routing",
                "extra": "fallback",
                "to": socket_path,
                "msg-from": self._id,
                "msg-serial": msg.serial
            }))

        # create DBusRemoteService if necessary
        async with self._lock:
            context=self._remotes.get(socket_path)
            if context is None:
                context=await self._connect_to_remote(socket_path)
                if self._dbus_name is not None:
                    context.remote.client_dbus_name=self._dbus_name

        return context.remote

    def _identify_noticeable_call(self, msg:Message, context:RemoteContext|None=None) -> NoticeableCall|None:
        if msg.message_type!=MessageType.METHOD_CALL:
            return None

        if msg.destination=="org.freedesktop.DBus" and msg.path=="/org/freedesktop/DBus" and msg.interface=="org.freedesktop.DBus":
            try:
                ncall=NoticeableCall(msg.member)
                return ncall
            except Exception:
                pass

        if context is not None:
            # TODO: make this a feature of the Selector objects
            if context.name_owners.get(msg.destination, msg.destination)=="org.freedesktop.portal.Desktop"  and msg.path=="/org/freedesktop/portal/desktop" and msg.interface=="org.freedesktop.portal.ScreenCast": # pyright: ignore
                try:
                    ncall=NoticeableCall(msg.member)
                    return ncall
                except Exception:
                    pass
        return None

    async def _say_hello(self, context:RemoteContext):
        # we need to say Hello() to get a connection name from the remote D-Bus server
        logging.info(json.dumps({
            "context": "say-hello",
            "extra": f"starting for {context.remote.socket_path}"
        }))
        if context.remote.client_dbus_name is None:
            txt=f"Remote D-Bus server '{context.remote.socket_path}' is not the 1st remote we have a connected to and yet client_dbus_name is None"
            logging.error(json.dumps({
                "text": txt
            }))
            raise Exception(txt)
        if context.remote.assigned_dbus_name is not None:
            txt=f"Remote D-Bus server '{context.remote.socket_path}' already has an assigned_dbus_name: {context.remote.assigned_dbus_name}"
            logging.error(json.dumps({
                "text": txt
            }))
            raise Exception(txt)

        hello_msg=self._hello_message
        if hello_msg is None:
            txt=f"Remote D-Bus server '{context.remote.socket_path}' is not the 1st remote we have a connected to and yet self._hello_message is None"
            logging.error(json.dumps({
                "text": txt
            }))
            raise Exception(txt)

        context.hello_queue=asyncio.Queue()
        try:
            async with asyncio.timeout(2):
                logging.debug(json.dumps({
                    "context": "sending-hello",
                    "to": self._id,
                    "msg-data": base64.b64encode(hello_msg.blob).decode() if hello_msg.blob is not None else None,
                    "msg-anc": base64.b64encode(hello_msg.ancillary_data).decode() if hello_msg.ancillary_data is not None else None,
                }))
                await context.remote.forward_message(hello_msg)
                reply=await context.hello_queue.get()
        except TimeoutError:
            txt="Did not get Hello() reply from the remote D-Bus service"
            logging.error(json.dumps({
                "text": txt
            }))
            raise Exception(txt)
        context.hello_queue=None

        conn_name=reply.unmarshall_body("s")
        logging.debug(json.dumps({
            "context": "info",
            "extra": f"D-Bus name for remote '{context.remote.socket_path}' is '{conn_name}' (transl. to/from {context.remote.client_dbus_name} will be done)",
            "msg-from": self._id,
            "msg-serial": reply.serial
        }))
        context.remote.assigned_dbus_name=conn_name
        logging.info(json.dumps({
            "context": "say-hello",
            "extra": f"done for {context.remote.socket_path}"
        }))

    async def _handle_remote_message(self, msg:Message, remote:DBusRemoteService):
        """Process a message received from a remote
        """
        context=self._remotes.get(remote.socket_path)
        if context is None:
            txt=f"CODEBUG: no associated remote for Unix socket '{remote.socket_path}'"
            syslog.syslog(syslog.LOG_EMERG, txt)
            logging.error(json.dumps({
                "text": txt
            }))

        if context is not None:
            match msg.message_type:
                case MessageType.METHOD_CALL:
                    # the remote is initiating a call on a client's object
                    self._routing_table[msg.serial]=MessageContext(message=msg, context=context)

                case MessageType.METHOD_RETURN | MessageType.ERROR:
                    # the remote is retuning information for a call the client made
                    if context.hello_queue is None:
                        msg_context=self._messages_contexts_table.pop(msg.reply_serial, None) # pyright: ignore
                        if msg.message_type in (MessageType.METHOD_RETURN, MessageType.ERROR):
                            if msg_context is not None:
                                if msg_context.callback is not None:
                                    msg_context.callback(msg_context.message, msg, self._remotes.get(remote.socket_path))
                    else:
                        # we are in a say Hello() phase
                        await context.hello_queue.put(msg)
                        return

                case MessageType.SIGNAL:
                    # the remote is sending a signal
                    if context.hello_queue is None:
                        pass
                    else:
                        # we are in the say Hello() process
                        if context.hello_queue is not None:
                            counter=0
                            while counter<50:
                                if context.hello_queue is not None:
                                    counter+=1
                                    await asyncio.sleep(0.05)
                                else:
                                    break
                            if context.hello_queue is not None:
                                txt=f"The say Hello() for {remote.socket_path} phase took too long, the NameAcquired signal is forwarded anyway"
                                syslog.syslog(syslog.LOG_WARNING, txt)
                                logging.warning(json.dumps({
                                    "text": txt
                                }))

                case _:
                    raise Exception(f"Unhandled message type {msg.message_type}")

        msg=remote.translate_to_client(msg)
        logging.debug(json.dumps({
            "context": "forwarding",
            "msg-from": remote.socket_path,
            "to": self._id,
            "msg-serial": msg.serial
        }))
        await self._buffer.forward_message(msg)

    def _call_method_Hello_replied(self, request:Message, reply:Message, context:RemoteContext):
        # catch the connection's unique name
        self._dbus_name=reply.unmarshall_body("s")
        context.remote.client_dbus_name=self._dbus_name # pyright: ignore
        context.remote.assigned_dbus_name=self._dbus_name # pyright: ignore
        logging.debug(json.dumps({
            "context": "discovery",
            "extra": f"D-Bus name is '{self._dbus_name}'",
            "object": self._id,
            "request-from": self._id,
            "request-serial": reply.serial,
            "reply-from": context.remote.socket_path,
            "reply-ref": reply.serial
        }))

    def _call_method_GetNameOwner_replied(self, request:Message, reply:Message, context:RemoteContext):
        name=request.unmarshall_body("s")
        name_owner=reply.unmarshall_body("s")
        context.name_owners[name_owner]=name
        logging.debug(json.dumps({
            "context": "discovery",
            "extra": f"Name '{name}' is owned by '{name_owner}'",
            "object": None,
            "request-from": self._id,
            "request-serial": request.serial,
            "reply-from": context.remote.socket_path,
            "reply-ref": reply.serial
        }))

    def _call_method_CreateSession_replied(self, request:Message, reply:Message, context:RemoteContext):
        path=reply.unmarshall_body("o")
        context.objects.add(path)
        logging.debug(json.dumps({
            "context": "discovery",
            "extra": f"CreateSession returned object '{path}'",
            "object": None,
            "request-from": self._id,
            "request-serial": request.serial,
            "reply-from": context.remote.socket_path,
            "reply-ref": reply.serial
        }))

    async def _handle_client_message(self, msg:Message):
        """Process a message received from the D-Bus client
        """
        # analyse the message's contents to build the router's context and choose the remote
        remotes:list[DBusRemoteService]=[]
        match msg.message_type:
            case MessageType.METHOD_CALL:
                # client is calling a method on a remote's object
                try:
                    # comput which remote the message will be sent to
                    remote=await self._get_remote_to_route_to(msg)
                    context=self._remotes.get(remote.socket_path)
                    remotes=[remote]

                    # open a connection to the remote of necessary
                    ncall=self._identify_noticeable_call(msg, context)
                    if ncall==NoticeableCall.Hello:
                        self._hello_message=msg
                    elif context is not None:
                        async with self._lock:
                            if context.remote.assigned_dbus_name is None:
                                # send an Hello message to that remote D-Bus server
                                await self._say_hello(context)

                    # update routing table for the return message if a return message is expected
                    if msg.reply_expected:
                        if context is None:
                            raise Exception("CODEBUG: context should not be None")
                        msg_context:MessageContext|None=None
                        match ncall:
                            case NoticeableCall.Hello:
                                msg_context=MessageContext(message=msg, context=context, callback=self._call_method_Hello_replied)
                            case NoticeableCall.GetNameOwner:
                                msg_context=MessageContext(message=msg, context=context, callback=self._call_method_GetNameOwner_replied)
                            case NoticeableCall.CreateSession:
                                msg_context=MessageContext(message=msg, context=context, callback=self._call_method_CreateSession_replied)
                            case _:
                                pass
                        if msg_context is not None:
                            self._messages_contexts_table[msg.serial]=msg_context
                except Exception as e:
                    txt=f"Failed to process client message #{msg}: {str(e)}"
                    syslog.syslog(syslog.LOG_ERR, txt)
                    logging.error(txt)

            case MessageType.METHOD_RETURN | MessageType.ERROR:
                # client is replying to a remote having called a method on the client
                msg_context=self._routing_table.pop(msg.reply_serial, None) # pyright: ignore
                if msg_context is None:
                    txt=f"Reply message {msg} not in routing table, ignoring but may have held important context information"
                    syslog.syslog(syslog.LOG_WARNING, txt)
                    logging.warning(json.dumps({
                        "text": txt
                    }))
                else:
                    remotes=[msg_context.context.remote]

            case MessageType.SIGNAL:
                # client is sending a signal
                # FIXME: we should only send to the remote the signal is for
                remotes=[context.remote for context in self._remotes.values()]

            case _:
                raise Exception(f"Unhandled message type {msg.message_type}")

        # forward message to remote(s)
        for remote in remotes:
            tmsg=remote.translate_to_remote(msg)
            logging.debug(json.dumps({
                "context": "forwarding",
                "to": remote.socket_path,
                "msg-from": self._id,
                "msg-serial": msg.serial
            }))
            await remote.forward_message(tmsg)

    async def _handle_all_incoming_messages(self):
        msg:Message|None=None
        while True:
            msg=await self._in_queue.get()
            if msg is None:
                return
            try:
                await self._handle_client_message(msg)
            except Exception as e:
                txt=f"Failed to handle client message {msg}: {str(e)}"
                syslog.syslog(syslog.LOG_WARNING, txt)
                logging.warning(json.dumps({
                    "text": txt
                }))

    async def run(self):
        """Process all the messages coming from the connected D-Bus client
        """
        asyncio.create_task(self._handle_all_incoming_messages())

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
            self.connected=False
