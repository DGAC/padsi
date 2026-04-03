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
import asyncio.subprocess
import json
import os
import socket
import sys
import syslog
from typing import Any

_debug=False

def recv_fd(conn:socket.socket) -> tuple[int|None, Any]:
    """Receive a file descriptor and JSON metadata"""
    (msg, ancdata, *_)=conn.recvmsg(4096, socket.CMSG_LEN(4))
    fd=None
    if ancdata!=[]:
        fds=array.array("i")  # for receiving the fd
        for cmsg_level, cmsg_type, cmsg_data in ancdata:
            if cmsg_level == socket.SOL_SOCKET and cmsg_type == socket.SCM_RIGHTS:
                fds.frombytes(cmsg_data[:4])
        fd=fds[0]
    return (fd, json.loads(msg.decode()))

class Job:
    def __init__(self, proc:asyncio.subprocess.Process, client_conn:socket.socket):
        self._proc=proc
        self._client_conn=client_conn

    @property
    def client_conn(self) -> socket.socket:
        return self._client_conn

    async def run(self):
        try:
            (stdout, stderr)=await self._proc.communicate()
            resp={
                "returncode": self._proc.returncode,
                "stdout": stdout.decode(),
                "stderr": stderr.decode()
            }
            self._client_conn.sendall(json.dumps(resp).encode())
        except asyncio.CancelledError:
            syslog.syslog(syslog.LOG_ERR, "Job cancelled")
            self._client_conn.sendall("ERROR: job cancelled".encode())
        except BrokenPipeError:
            pass
        except Exception as e:
            syslog.syslog(syslog.LOG_ERR, f"ERROR: {str(e)} (run() exception type: {type(e)})")
            self._client_conn.sendall(f"ERROR: {str(e)}".encode())
        finally:
            self._client_conn.close()

class Server:
    def __init__(self, socket_path:str):
        # set up listening server
        self._socket_path=socket_path
        self._server:socket.socket|None=None
        self._dirs_map:dict[str,str]={}
        self._jobs:dict[asyncio.Task,Job]={}

    def declare_dirs_map(self, b_dir:str, h_dir:str):
        if not os.path.isabs(b_dir):
            raise Exception(f"Invalid non absolute path '{b_dir}' in bubble")
        if not os.path.isabs(h_dir):
            raise Exception(f"Invalid non absolute path '{h_dir}' in host")
        self._dirs_map[b_dir]=h_dir

    def map_bubble_dir_to_host(self, mountpoint:str) -> str:
        for (b_dir, h_dir) in self._dirs_map.items():
            if mountpoint.startswith(b_dir):
                return os.path.join(h_dir, mountpoint[len(b_dir)+1:])
        raise Exception(f"Invalid mount point '{mountpoint}'")

    async def _handle_fusermount(self, fd: int|None, data:Any) -> asyncio.subprocess.Process:
        mountpoint=data["mp"]
        h_mountpoint=self.map_bubble_dir_to_host(mountpoint)

        args=["/usr/bin/fusermount3"]
        if data.get("o"):
            args+=["-o", data.get("o")]
        for opt in ("V", "u", "q", "z"):
            if data.get(opt):
                args+=[f"-{opt}"]
        args.append(h_mountpoint)
        env=os.environ.copy()
        if fd is None:
            syslog.syslog(syslog.LOG_INFO, f"Running {args}")
            return await asyncio.create_subprocess_exec(*args, env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        else:
            syslog.syslog(syslog.LOG_INFO, f"Running {args} ({fd=})")
            env["_FUSE_COMMFD"]=str(fd)
            return await asyncio.create_subprocess_exec(*args, pass_fds=(fd,), env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)

    async def _handle_umount(self, fd: int|None, data:Any) -> asyncio.subprocess.Process:
        if fd is not None:
            raise Exception(f"Unexpected passed file descriptor {fd}")
        mountpoint=data["mp"]
        args=data["args"]
        if mountpoint is not None:
            h_mountpoint=self.map_bubble_dir_to_host(mountpoint)
            args.append(h_mountpoint)

        args=["/usr/bin/umount"]+args
        syslog.syslog(syslog.LOG_INFO, f"Running {args}")
        env=os.environ.copy()
        return await asyncio.create_subprocess_exec(*args, env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)

    def _job_done(self, task:asyncio.Task):
        job=self._jobs.get(task)
        if job is None:
            syslog.syslog(syslog.LOG_ERR, f"CODEBUG: task {task} is not associated to any job")
        elif _debug:
            syslog.syslog(syslog.LOG_DEBUG, f"Request done conn={job.client_conn}")
        del self._jobs[task]

    async def run(self):
        # Ensure socket does not already exist
        if os.path.exists(self._socket_path):
            os.unlink(self._socket_path)
        self._server=socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server.bind(self._socket_path)
        self._server.setblocking(False)
        os.chmod(self._socket_path, 0o600)
        self._server.listen()

        # wait for client connections
        try:
            loop=asyncio.get_running_loop()
            while True:
                if _debug:
                    syslog.syslog(syslog.LOG_DEBUG, f"now listening {self._socket_path=}")
                try:
                    (conn, _)=await loop.sock_accept(self._server)
                except BrokenPipeError:
                    break

                try:
                    if _debug:
                        syslog.syslog(syslog.LOG_DEBUG, f"New connection {conn=}")
                    (fd, data)=recv_fd(conn)
                    if _debug:
                        syslog.syslog(syslog.LOG_DEBUG, f"Request {data=}")
                    match data.get("prog"):
                        case "fusermount":
                            proc=await self._handle_fusermount(fd, data)
                        case "umount":
                            proc=await self._handle_umount(fd, data)
                        case _:
                            raise Exception(f"Unhandled proxied program '{data.get('prog')}'")

                    job=Job(proc, conn)
                    task=asyncio.create_task(job.run())
                    self._jobs[task]=job
                    task.add_done_callback(self._job_done)
                except Exception as e:
                    syslog.syslog(syslog.LOG_ERR, str(e))
                    resp={
                        "returncode": 1,
                        "stdout": None,
                        "stderr": str(e)
                    }
                    conn.sendall(json.dumps(resp).encode())
                    conn.close()
        except Exception as e:
            syslog.syslog(syslog.LOG_ERR, f"Really unexpected error: {str(e)}")
        finally:
            os.unlink(self._socket_path)

async def main(server:Server):
    await server.run()

if __name__=="__main__":
    # parse command line arguments
    if len(sys.argv)<4 or len(sys.argv)%2!=0:
        raise Exception(f"Usage: {__file__} <socket path> <dir in bubble> <corresponding dir in host> [...]")
    server=Server(sys.argv[1])

    index=2
    dirs_map:dict[str,str]={}
    while index<len(sys.argv):
        server.declare_dirs_map(sys.argv[index], sys.argv[index+1])
        index+=2
    asyncio.run(main(server))
