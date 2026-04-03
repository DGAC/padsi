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

import asyncio
import base64
import json
import os
import signal
import subprocess
import sys
import syslog
import time
import unittest

import prctl
import psutil

from padsi.simple_comm import Client, Message, MessageType, Server


class MessagesTest(unittest.TestCase):
    def test_0(self):
        data={"testkey": "VALUE", "counter": 4}
        req=Message(MessageType.REQUEST, data)
        ser=req.to_str(34)
        req2=Message.from_str(ser)
        self.assertEqual(req2.req_id, 34)
        self.assertEqual(req2.data, data)

class TestServer(Server):
    async def handle_request(self, request:Message) -> Message:
        syslog.syslog(syslog.LOG_DEBUG, f"Test server: handling request {request.to_str()}")
        if "fail" in request.data:
            raise Exception(f"Failed: {request.data['fail']}")
        if "counter" in request.data:
            request.data["counter"]=int(request.data["counter"])+1
        return Message(MessageType.REPLY, request.data)

class MainTest(unittest.TestCase):
    def test_ok(self):
        # simple calls
        async def run_test():
            data=[
                {
                    "request": {"testkey": "VALUE", "counter": 4},
                    "reply":  {"testkey": "VALUE", "counter": 5}
                },
                {
                    "request": {"testkey": "VALUE2", "counter": 8},
                    "reply":  {"testkey": "VALUE2", "counter": 9}
                }
            ]
            server=TestServer()
            await server.serve_client([__file__, "CLIENT", "0", base64.b64encode(json.dumps(data).encode()).decode()])
        asyncio.run(run_test())

    def test_exception(self):
        # simple call which raises an exception
        async def run_test():
            data=[
                {
                    "request": {"fail": "bad"},
                    "reply": {"exception": "Failed: bad"}
                }
            ]
            server=TestServer()
            await server.serve_client([__file__, "CLIENT", "0", base64.b64encode(json.dumps(data).encode()).decode()])
        asyncio.run(run_test())

    def test_cancel_client(self):
        # test cancelling client
        async def run_test():
            data=[
                {
                    "request": {"testkey": "VALUE", "counter": 4},
                    "reply":  {"testkey": "VALUE", "counter": 5}
                }
            ]
            server=TestServer()
            asyncio.create_task(server.serve_client([__file__, "CLIENT", "-2", base64.b64encode(json.dumps(data).encode()).decode()]))
            await asyncio.sleep(1)
            self.assertEqual(server.is_running, False)

        asyncio.run(run_test())

    def test_kill_client(self):
        # test when client is killed by server
        async def run_test():
            data=[
                {
                    "request": {"testkey": "VALUE", "counter": 4},
                    "reply":  {"testkey": "VALUE", "counter": 5}
                }
            ]
            server=TestServer()
            task=asyncio.create_task(server.serve_client([__file__, "CLIENT", "10", base64.b64encode(json.dumps(data).encode()).decode()]))
            # give time to start and run client
            await asyncio.sleep(0.2)
            # kill client
            assert(server.client_pid is not None)
            os.kill(server.client_pid, signal.SIGTERM)
            # give time to realize client has been killed
            await asyncio.sleep(0.2)
            self.assertEqual(task.done(), True)
            self.assertEqual(task.cancelled(), False)
            self.assertIn("Client process killed with signal", str(task.exception()))
            self.assertEqual(server.is_running, False)
        asyncio.run(run_test())

    def test_kill_server(self):
        # test when the server process dies before the client
        # => requires running ourself as a SERVER
        prctl.set_child_subreaper(1) # pyright: ignore
        proc=subprocess.Popen([__file__, "SERVER"])
        syslog.syslog(syslog.LOG_DEBUG, f"unit test process: {os.getpid()}")
        syslog.syslog(syslog.LOG_DEBUG, f"server process: {proc.pid}")
        time.sleep(0.2)
        srv_p=psutil.Process(proc.pid)
        children=srv_p.children()
        self.assertEqual(len(children), 1)
        clnt_p=children[0]
        syslog.syslog(syslog.LOG_DEBUG, f"client process: {clnt_p.pid}")
        syslog.syslog(syslog.LOG_DEBUG, f"killing server process: {proc.pid}")
        os.kill(proc.pid, signal.SIGKILL)
        status=proc.wait()
        syslog.syslog(syslog.LOG_DEBUG, f"process {proc.pid} has been waited for and returned status {status}")
        time.sleep(2)
        syslog.syslog(syslog.LOG_DEBUG, f"waiting for client process {clnt_p.pid}")
        (_, status)=os.waitpid(clnt_p.pid, 0)
        self.assertEqual(status>>8, 3)


if __name__=='__main__':
    # this program can be launched using different modes depending on the command line:
    # - as a normal Python unit test
    # - as a CLIENT process spawned by a Server
    # - as a SERVER process which will itself spawn as a CLIENT process...

    if len(sys.argv)>1 and sys.argv[1]=="CLIENT":
        # we are launched (by ourself) to implement a CLIENT which will send commands to the server
        if len(sys.argv)!=4:
            raise Exception("CODEBUG in the test implementation")

        extra_action=int(sys.argv[2])
        testdata=sys.argv[3]
        requests=json.loads(base64.b64decode(testdata.encode()).decode())
        syslog.syslog(syslog.LOG_DEBUG, f"CLIENT: requests to do: {requests}")
        async def run_test():
            client=Client()
            task=asyncio.create_task(client.run())
            await asyncio.sleep(0.01) # let the run() task actually be scheduled
            for item in requests:
                msg=Message(MessageType.REQUEST, item["request"])
                try:
                    reply=await client.call_server(msg)
                    exp_reply=Message(MessageType.REPLY, item["reply"])
                    assert(reply is not None)
                    assert(reply.req_id is not None)
                    exp_reply.req_id=reply.req_id
                    if reply!=exp_reply:
                        print(f"Expected {exp_reply}, got {reply}", file=sys.stderr)
                        sys.exit(1)
                except Exception as e:
                    exp_str=item["reply"].get("exception")
                    if exp_str is None:
                        print(f"Got unexpected exception: {e}", end="", file=sys.stderr)
                        sys.exit(2)
                    else:
                        exp_reply=Exception(exp_str)
                        if type(e)!=type(exp_reply) or str(e)!=str(exp_reply): # pyright: ignore
                            print(f"Expected {exp_reply}, got {e}", end="", file=sys.stderr)
                            sys.exit(2)

            if extra_action>0:
                await asyncio.sleep(extra_action)
            elif extra_action==-1:
                # make a last call to the server which must have been killed
                await asyncio.sleep(1)
                msg=Message(MessageType.REQUEST, None)
                try:
                    syslog.syslog(syslog.LOG_DEBUG, f"after server process was killed, sending request, PPID: {os.getppid()}")
                    reply=await client.call_server(msg)
                    syslog.syslog(syslog.LOG_ERR, f"received unexpected {reply=}")
                except Exception as e:
                    syslog.syslog(syslog.LOG_DEBUG, f"got expected exception {e=}, exiting with status 3")
                    sys.exit(3)
            elif extra_action==-2:
                # client shuts down by itself
                syslog.syslog(syslog.LOG_DEBUG, "Client auto cancelling task...")
                task.cancel()
                syslog.syslog(syslog.LOG_DEBUG, "Client auto cancelled task")

            syslog.syslog(syslog.LOG_DEBUG, f"CLIENT process {os.getpid()} terminates")
        asyncio.run(run_test())

    elif len(sys.argv)>1 and sys.argv[1]=="SERVER":
        # we are launched (by ourself) to implement a server (which will itself spawn a client which will send commands to us)
        async def run_test():
            data=[
                {
                    "request": {"testkey": "VALUE", "counter": 4},
                    "reply":  {"testkey": "VALUE", "counter": 5}
                }
            ]
            server=TestServer()
            await server.serve_client([__file__, "CLIENT", "-1", base64.b64encode(json.dumps(data).encode()).decode()])
        asyncio.run(run_test())
        syslog.syslog(syslog.LOG_DEBUG, f"server process {os.getpid()} terminated")

    else:
        unittest.main()
