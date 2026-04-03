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


#
# Simple mechanism to:
# - spawn a new client process
# - communicate and reply to requests using messages passed via pipes
#

from __future__ import annotations

import asyncio
import enum
import json
import os
import signal
import syslog
from typing import Any

_debug=False

class MessageType(int, enum.Enum):
    REQUEST= 0
    REPLY= 1

class Message:
    """Class to convey messages between client and server processes
    """
    def __init__(self, msg_type:MessageType, data:Any=None):
        self._type=msg_type
        self._data=data
        self._req_id=None

    @property
    def msg_type(self) -> MessageType:
        return self._type

    @property
    def req_id(self) -> int|None:
        return self._req_id

    @req_id.setter
    def req_id(self, req_id:int):
        self._req_id=req_id

    @property
    def data(self):
        return self._data

    def __repr__(self) -> str:
        return f"Message({self._type.value}, {self._req_id}, {json.dumps(self._data)})"

    def __eq__(self, other:object) -> bool:
        return str(self)==str(other)

    def to_str(self, req_id:int|None=None) -> str:
        """Transform the object into a string, specifying a request ID if necessary
        """
        if req_id is not None and self._req_id is not None:
            raise Exception(f"Message already has request ID {self._req_id}")
        elif req_id is None and self._req_id is None:
            raise Exception("Message does not have a request ID")
        data={
            "type": self._type.value,
            "req-id": req_id if req_id is not None else self._req_id,
            "data": self._data,
        }
        return json.dumps(data)

    @classmethod
    def from_str(cls, str_repr:str) -> Message:
        data=json.loads(str_repr)
        obj=cls(MessageType(data["type"]), data["data"])
        obj.req_id=data["req-id"]
        return obj

class ServerProcessDied(Exception):
    pass

class ClientProcessKilled(Exception):
    pass

async def _pipes_to_streams(reader_fd:int, writer_fd:int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    loop=asyncio.get_event_loop()
    reader=asyncio.StreamReader()
    reader_protocol=asyncio.StreamReaderProtocol(reader)
    await loop.connect_read_pipe(lambda: reader_protocol, os.fdopen(reader_fd, "rb"))

    (writer_transport, _)=await loop.connect_write_pipe(asyncio.streams.FlowControlMixin, os.fdopen(writer_fd, "wb"))
    writer=asyncio.StreamWriter(writer_transport, None, None, loop) # pyright: ignore

    return (reader, writer)

class Client:
    def __init__(self):
        self._system_service_died=False
        self._requests_counter:int=0
        self._requests_queues:dict[int,asyncio.Queue]={}
        self._is_running:bool=False
        self._reader:asyncio.StreamReader|None=None
        self._writer:asyncio.StreamWriter|None=None
        self._lock=asyncio.Lock()

    async def _prepare(self):
        # preparations (close unused FDs and wrap pipe fds in asyncio streams)
        try:
            s2c_r=int(os.environ["S2C_R"])
            c2s_w=int(os.environ["C2S_W"])
        except Exception as e:
            raise Exception(f"Client process was not run with the expected environment (S2C_R and C2S_W): {str(e)}")
        (self._reader, self._writer)=await _pipes_to_streams(s2c_r, c2s_w)

    @property
    def server_died(self) -> bool:
        """True if we have detected that the server process
        has died
        """
        return self._system_service_died

    @property
    def last_request_id(self) -> int:
        """Id of the last request"""
        return self._requests_counter

    async def run(self):
        """Run the background task which handles replies from the server
        """
        if self._is_running:
            raise Exception("Client is already being run")

        async with self._lock:
            if self._reader is None:
                await self._prepare()

        self._is_running=True
        while True:
            try:
                if self._reader is None:
                    raise Exception("CODEBUG: self._reader should not be None")
                data=await self._reader.readline()
                if _debug:
                    syslog.syslog(syslog.LOG_DEBUG, f"CLIENT RECV: {data}")
                if not data:
                    break
                try:
                    # prepare reply object
                    data=data.decode()
                    if data[-1]=="\n":
                        data=data[:-1]
                    if data.startswith("EXCEPTION:"):
                        data=json.loads(data[10:])
                        res=Exception(data["exception"])
                        req_id=data["req-id"]
                    elif data.startswith("RESULT:"):
                        res=Message.from_str(data[7:])
                        req_id=res.req_id
                    else:
                        raise Exception("CODEBUG: reply should start with 'RESULT:' or 'EXCEPTION:'")

                    # transmit reply object to task
                    queue=self._requests_queues.get(req_id)
                    if queue is None:
                        syslog.syslog(syslog.LOG_ERR, f"Received message but it's not a reply to a call (request ID '{req_id}')")
                    else:
                        queue.put_nowait(res)
                        del self._requests_queues[req_id]
                except Exception:
                    syslog.syslog(syslog.LOG_ERR, f"Invalid reply '{data}'")
            except asyncio.exceptions.CancelledError:
                if _debug:
                    syslog.syslog(syslog.LOG_DEBUG, "Client.run() has been cancelled")
                break
        self._is_running=False

    async def call_server(self, request:Message) -> Message|None:
        """Send a message to the server and return its reply
        May raise an exception if the server raised an exception
        """
        async with self._lock:
            if self._reader is None:
                await self._prepare()

        if self._system_service_died:
            raise ServerProcessDied()
        if not self._is_running:
            # let self.run() start
            await asyncio.sleep(0.1)
            if not self._is_running:
                raise Exception("Cient's run() task has not yet been started or has been stopped")

        self._requests_counter+=1
        req_id=self._requests_counter
        raw=request.to_str(req_id)

        queue=asyncio.Queue()
        self._requests_queues[req_id]=queue

        try:
            if _debug:
                syslog.syslog(syslog.LOG_DEBUG, f"CLIENT SEND: {raw}")
            if self._writer is None:
                raise Exception("CODEBUG: self._writer should not be None")
            self._writer.write((raw+"\n").encode())

            res=await queue.get()
            if isinstance(res, Exception):
                raise res
            return res
        except (BrokenPipeError, ValueError):
            self._system_service_died=True
            raise ServerProcessDied()


class Server:
    """Class to act as a server for a single client process
    """
    def __init__(self, lock:asyncio.Lock|None=None):
        self._process:asyncio.subprocess.Process|None=None
        self._jobs:set[asyncio.Task]=set()
        self._event=asyncio.Event()
        if lock is None:
            lock=asyncio.Lock()
        self._lock=lock # prevent handling requests while starting the client process

    @property
    def client_pid(self) -> int|None:
        return None if self._process is None else self._process.pid

    @property
    def is_running(self) -> bool:
        return self._process is not None

    def pre_client_spawn(self):
        """Function called before spawning the process, can be subclassed to do anything actually usefull
        """
        pass

    def post_client_spawn(self):
        """Function called after spawning the process, can be subclassed to do anything actually usefull
        """
        pass

    async def _implement_job(self, line:str, writer:asyncio.StreamWriter):
        """Async function to handle a request and send the reply when done"""
        req=None
        try:
            req=Message.from_str(line)
            res=await self._handle_request(req)
            res.req_id=req.req_id # pyright: ignore
            ser="RESULT:"+res.to_str()
        except Exception as e:
            syslog.syslog(syslog.LOG_ERR, f"Could not handle the request '{line}': {str(e)}")
            if req is not None:
                data={
                    "req-id": req.req_id,
                    "exception": str(e),
                }
            else:
                data={
                    "req-id": None,
                    "exception": str(e),
                }
            ser="EXCEPTION:"+json.dumps(data)

        # send the reply to the client
        if _debug:
            syslog.syslog(syslog.LOG_DEBUG, f"SERVER SEND: {ser}")
        if self._process is not None:
            writer.write((ser+"\n").encode())

    async def _handle_client_requests(self, writer:asyncio.StreamWriter, reader:asyncio.StreamWriter):
        while True:
            if _debug:
                syslog.syslog(syslog.LOG_DEBUG, "...waiting for a request from client")
            try:
                line=await reader.readline() # pyright: ignore
                if _debug:
                    syslog.syslog(syslog.LOG_DEBUG, f"SERVER RECV: {line}")
                if not line:
                    if self._process is None: # may have been shut down in the meanwhile
                        break
                    retcode=self._process.returncode
                    while retcode is None:
                        await asyncio.sleep(0.01)
                        retcode=self._process.returncode

                    if retcode!=0:
                        stderr=(await self._process.stderr.read()).decode() # pyright: ignore
                        if stderr:
                            msg=f"Client process died with return code {retcode} ({stderr})"
                            syslog.syslog(syslog.LOG_ERR, msg)
                            e=Exception(msg)
                        elif retcode in (-signal.SIGKILL, -signal.SIGTERM):
                            e=ClientProcessKilled(f"Client process killed with signal {-retcode}")
                        else:
                            msg=f"Client process died with return code {retcode}"
                            syslog.syslog(syslog.LOG_ERR, msg)
                            e=Exception(msg)

                        await self._process.wait()
                        self._process=None
                        self._event.set()
                        raise e
                    break
                job=asyncio.create_task(self._implement_job(line, writer))
                self._jobs.add(job)
                job.add_done_callback(self._jobs.remove)
            except asyncio.exceptions.CancelledError:
                if _debug:
                    syslog.syslog(syslog.LOG_DEBUG, "_handle_client_requests() task cancelled")
                break
        self._event.set()

    async def serve_client(self, args:list[str]):
        """Spawns a client and serve its requests up to when the client terminates
        """
        if self._process is not None:
            raise Exception("Client process is already running")

        # spawn the client process
        async with self._lock:
            try:
                if _debug:
                    syslog.syslog(syslog.LOG_DEBUG, "serve_client() started")
                self.pre_client_spawn()
                (s2c_r, s2c_w)=os.pipe()
                (c2s_r, c2s_w)=os.pipe()
                cenv=os.environ.copy()
                cenv["S2C_R"]=str(s2c_r)
                cenv["C2S_W"]=str(c2s_w)
                if _debug:
                    syslog.syslog(syslog.LOG_DEBUG, f"serve_client(): starting process: {' '.join(args)}")
                self._process=await asyncio.create_subprocess_exec(*args, stderr=asyncio.subprocess.PIPE, pass_fds=(s2c_r, c2s_w), env=cenv)
                if _debug:
                    syslog.syslog(syslog.LOG_DEBUG, "serve_client() process started")
                self.post_client_spawn()
                if _debug:
                    syslog.syslog(syslog.LOG_DEBUG, "serve_client() done")
            except Exception as e:
                raise Exception(f"Failed to start client process: {str(e)}")

        # preparations (close unused FDs and wrap pipe fds in asyncio streams)
        os.close(s2c_r)
        os.close(c2s_w)
        (reader, writer)=await _pipes_to_streams(c2s_r, s2c_w)

        # handle the client's requests
        # we start a new task because web want so serve client requests handling
        # up until the client has terminated, which means that cancelling the task associated
        # to this function must not interrupt client requests handling.
        task=None
        try:
            task=asyncio.create_task(self._handle_client_requests(writer, reader)) # pyright: ignore
            await self._event.wait()
            if _debug:
                syslog.syslog(syslog.LOG_DEBUG, "_handle_client_requests() has terminated")
        except asyncio.exceptions.CancelledError:
            if _debug:
                syslog.syslog(syslog.LOG_DEBUG, "_handle_client_requests() task cancelled")

        # shutdown (still handling client requests if possible)
        if self._process is not None:
            if _debug:
                syslog.syslog(syslog.LOG_DEBUG, f"Shutdown, sending TERM to client with PID {self._process.pid}")
            try:
                self._process.send_signal(signal.SIGTERM)
                res=await self._process.wait()
                if _debug:
                    syslog.syslog(syslog.LOG_DEBUG, f"Shutdown: terminated, retcode from terminated client {res}")
            except ProcessLookupError:
                pass
            except Exception as e:
                syslog.syslog(syslog.LOG_ERR, f"Error in serve_client()'s shutdown: {str(e)}")
            finally:
                self._process=None

        # stop handling clients requests if not yet done
        if task is not None:
            task.cancel()
            await asyncio.wait([task])
            e=task.exception() # avoid useless warnings
            if e is not None and not isinstance(e, ClientProcessKilled):
                raise e

    async def _handle_request(self, request:Message) -> Message:
        async with self._lock:
            return await self.handle_request(request)

    async def handle_request(self, request:Message) -> Message:
        """Function called when the client process sent a request
        Must return a Message with the same request ID as the request.
        """
        raise Exception("handle_request() needs to be implemented but the class inheriting Server")
