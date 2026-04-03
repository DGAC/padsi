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
import json
import logging
import socket
import struct
import syslog
import typing

from .message import Message, MessageIncomplete


################ implementing recvmsg() with asyncio while not part of Python's core
# Refer to:
#  - https://stackoverflow.com/questions/38235997/how-to-implement-recvmsg-with-asyncio
#  - https://discuss.python.org/t/expanding-asyncio-support-for-socket-apis/19277
#  - https://github.com/python/cpython/pull/114857
#
def _sock_recvmsg(loop, fut, registered, sock, bufsize, ancbufsize):
    self=loop
    fd=sock.fileno()
    if registered:
        self.remove_reader(fd)
    if fut.cancelled():
        return

    try:
        data=sock.recvmsg(bufsize, ancbufsize)
    except (BlockingIOError, InterruptedError):
        self.add_reader(fd, self._sock_recvmsg, fut, True, sock, bufsize, ancbufsize)
    except Exception as exc:
        fut.set_exception(exc)
    else:
        fut.set_result(data)

def sock_recvmsg(loop, sock, bufsize, ancbufsize=0):
    self = loop
    if sock.gettimeout()!=0:
        raise ValueError("The socket must be non-blocking")
    fut = asyncio.futures.Future(loop=self)
    self._sock_recvmsg(fut, False, sock, bufsize, ancbufsize)
    return fut

asyncio.unix_events._UnixSelectorEventLoop._sock_recvmsg = _sock_recvmsg # pyright: ignore
asyncio.unix_events._UnixSelectorEventLoop.sock_recvmsg = sock_recvmsg # pyright: ignore
#
#
################

class ConnectionClosed(Exception):
    pass

class SocketBuffer:
    """Object to read data from a socket and keep track of data already used and data not yet used.
    Also handles file descriptors received via the socket
    """
    chunk_size=1024 # size of a read chunk and of each "buffer extension"
    max_size=1024*1024*128 # max size of a D-Bus message

    def __init__(self, sock:socket.socket, cnc_id:str|None=None):
        self._sock=sock
        self._cnc_id=cnc_id
        self._buffer:bytearray=bytearray(b"\x00"*SocketBuffer.chunk_size)
        self._view:memoryview=memoryview(self._buffer)
        self._buffer_len=len(self._buffer)
        self._index:int=0 # index where the _next_ data will come
        self._passed_fds:dict[range, list[int]]={} # key=range of indexes of the data RECV'ed from the socket, value=FDs actually received in that batch

    @property
    def length(self) -> int:
        """Length in bytes of the data currently stored in the buffer
        """
        return self._index

    async def read(self):
        """Read some data from the socket and store it internally in the object
        """
        loop=asyncio.get_running_loop()
        #loop.set_debug(True)
        (bdata, ancdata, flags, addr)=await loop.sock_recvmsg(self._sock, SocketBuffer.chunk_size, socket.CMSG_SPACE(struct.calcsize('i'))) # pyright: ignore
        if bdata==b'':
            raise ConnectionClosed()
        blen=len(bdata)

        received_fds:list[int]=[]
        if ancdata!=[]:
            for cmsg_level, cmsg_type, cmsg_data in ancdata:
                if cmsg_level==socket.SOL_SOCKET and cmsg_type==socket.SCM_RIGHTS:
                    fd=struct.unpack('i', cmsg_data[:4])[0]
                    received_fds.append(fd)
                    try:
                        import os
                        os.stat(fd)
                        txt=f"Received FD {fd}"
                        logging.debug(json.dumps({
                            "context": "fd-received",
                            "from": self._cnc_id,
                            "text": txt
                        }))
                    except Exception as e:
                        txt=f"Received FD {fd}, NOK: {str(e)}"
                        syslog.syslog(syslog.LOG_WARNING, txt)
                        logging.warning(json.dumps({
                            "context": "fd-received",
                            "from": self._cnc_id,
                            "text": txt
                        }))

        if blen+self._index>self._buffer_len:
            # extend buffer
            if self._buffer_len>=SocketBuffer.max_size:
                raise Exception("Maximum buffer size reached")
            self._view=None # pyright: ignore
            self._buffer+=bytearray(b"\x00"*SocketBuffer.chunk_size)
            self._buffer_len+=SocketBuffer.chunk_size
            self._view=memoryview(self._buffer)

        self._view[self._index:self._index+blen]=bdata
        if len(received_fds)>0:
            self._passed_fds[range(self._index, self._index+blen)]=received_fds
        self._index+=blen

    async def write(self, data:bytes, ancillary_data=None):
        """Write some data to the socket
        """
        # TODO: make it async
        if ancillary_data is None:
            sent=self._sock.sendmsg([data])
        else:
            sent=self._sock.sendmsg([data], ancillary_data)
        if sent!=len(data):
            txt=f"Failed to send all data to socket: {sent} out of {len(data)}"
            syslog.syslog(syslog.LOG_ERR, txt)
            logging.error(json.dumps({
                "text": txt
            }))
            raise Exception(txt)

    async def forward_message(self, message:Message):
        """Write the specified message to the socket"""
        await self.write(message.blob, message.ancillary_data)

    def _pop_data(self, length:int|None=None) -> tuple[bytes,typing.Any|None]:
        """Get the len first bytes of data from the buffer
        """
        if length is None:
            length=self._index
        elif length>self._index:
            raise Exception(f"Can't use more than {self._index} bytes of data from the buffer")
        data=bytes(self._buffer[:length])
        self._view[0:self._buffer_len-length]=bytes(self._buffer[length:]) # shift data to the beginning
        self._view[self._buffer_len-length:]=bytes(b"\x00"*length)

        # adapt FD indexes
        ret_fds=[]
        updated_fds={}
        for (r, fds) in self._passed_fds.items():
            if r.stop<=length:
                ret_fds+=fds
            else:
                r2=range(r.start-length, r.stop-length)
                updated_fds[r2]=fds
        if len(updated_fds)!=len(self._passed_fds):
            logging.debug(json.dumps({
                "context": "fd-use",
                "from": self._cnc_id,
                "text": f"Using FD {ret_fds}"
            }))
        self._passed_fds=updated_fds
        self._index-=length

        return (data, None if len(ret_fds)==0 else ret_fds)

    async def readline(self) -> bytes:
        """Read data up to receiving "\n"
        """
        o=ord("\n")
        index=0
        while True:
            while index<self._index:
                if self._buffer[index]==o:
                    (data, _)=self._pop_data(index+1)
                    return data
                index+=1
            await self.read()

    def get_dbus_message(self) -> Message|None:
        """Get a DBUs message from the data currently stored in the buffer, or
        None if there is no data or not enough data to make a D-Bus message
        """
        try:
            msg=Message(bytes(self._buffer[:self._index]))
            (data, passed_fds)=self._pop_data(msg.length)
            if passed_fds:
                msg.passed_file_descriptors=passed_fds

            msg.from_descr=self._cnc_id if self._cnc_id is not None else None # pyright: ignore
            logging.debug(json.dumps({
                "context": "received",
                "msg-from": msg.from_descr,
                "msg-serial": msg.serial,
                "msg-type": msg.message_type.value,
                "msg-to": msg.destination,
                "msg-interface": msg.interface,
                "msg-member": msg.member,
                "msg-path": msg.path,
                "msg-signature": msg.signature,
                "msg-sender": msg.sender,
                "msg-data": base64.b64encode(msg.blob).decode() if msg.blob is not None else None,
                "msg-anc": f"{msg.ancillary_data}" if msg.ancillary_data is not None else None
            }))

            return msg
        except MessageIncomplete:
            if self._index> 2**27:
                raise Exception("Invalid message (too big or malformed)")
            return None
        except Exception as e:
            txt=f"Invalid message(?) {str(e)}"
            syslog.syslog(syslog.LOG_ERR, txt)
            logging.error(json.dumps({
                "text": txt
            }))
            raise e
