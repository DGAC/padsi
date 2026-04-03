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
import enum
import logging
import logging.handlers
import os
import platform
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import PurePosixPath

from aiohttp import web
from aiohttp.web_response import json_response
from common import OSAgent, Runner, admin_port


class OSType(str, enum.Enum):
    LINUX = "Linux"
    WINDOWS = "Windows"

@dataclass
class Task:
    """Represents a (long) task running
    """
    id:str
    descr:str
    args:list[str]|None=None
    runner:Runner|None=None
    returncode:int|None=None # <0 on error
    exception:Exception|None=None
    res:dict|None=None # result of the execution of program, or None
    asynctask:asyncio.Task|None=None

    def run(self, os_agent:OSAgent):
        if self.args is None:
            raise Exception(f"Could not run task {self.descr} ({self.id}): no arguments provided")
        self.asynctask=asyncio.create_task(self._run(os_agent))

    @staticmethod
    def _get_stdout_stderr(stdout:bytes, stderr:bytes) -> tuple[bool, str, bool, str]:
        """Get the process's stdout and stderr as strings
        returns: (stdout is base64 encoded, stdout as str, stderr is base64 encoded, stderr)
        """
        eout=False
        if isinstance(stdout, bytes):
            try:
                dstdout=stdout.decode()
            except Exception:
                eout=True
                dstdout=base64.b64encode(stdout).decode()
        eerr=False
        if isinstance(stderr, bytes):
            try:
                dstderr=stderr.decode()
            except Exception:
                eerr=True
                dstderr=base64.b64encode(stderr).decode()
        return (eout, dstdout, eerr, dstderr)

    async def _run(self, os_agent:OSAgent):
        """Start and monitor a process up to when it terminates
        """
        try:
            cenv=os.environ.copy()
            cenv["PADSI_ETC_DIR"]=os.path.join(os_agent.management_mount_point, "etc")
            cenv["PADSI_LIB_DIR"]=os.path.join(os_agent.management_mount_point, "lib")
            cenv["PADSI_USER_DIR"]=os.path.join(os_agent.management_mount_point, "user")
            cenv["PYTHONPATH"]=cenv["PADSI_LIB_DIR"]

            if self.runner is None:
                msg="CODEBUG: runner should not be None"
                os_agent.logger.error(msg)
                raise Exception(msg)

            rargs=self.runner.get_arguments(self.args)
            os_agent.logger.info(f"Running: {' '.join(rargs)}")
            proc=await asyncio.create_subprocess_exec(*rargs, stdin=None, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=cenv)
            (stdout, stderr)=await proc.communicate()
            os_agent.logger.info(f"{self.args} finished, retcode: {proc.returncode}")
            (eout, stdout, eerr, stderr)=Task._get_stdout_stderr(stdout, stderr)

            self.res={
                "status": proc.returncode,
                "stdout-b64": eout,
                "stdout": stdout,
                "stderr-b64": eerr,
                "stderr": stderr
            }
            self.returncode=proc.returncode
        except Exception as e:
            self.returncode=-1
            self.exception=e
            os_agent.logger.error(f"ERR: {str(e)}")

    @property
    async def result(self) -> dict|None:
        if self.asynctask is None:
            return None
        if self.returncode is None:
            return None
        await self.asynctask
        self.asynctask=None
        if self.returncode<0:
            raise self.exception if self.exception is not None else Exception("???")
        return self.res

    @staticmethod
    def non_finished_result() -> dict:
        """Response similar in its structure to the result of a finished task but with
        empty safe values
        """
        return {
            "status": None,
            "stdout-b64": None,
            "stdout": None,
            "stderr-b64": None,
            "stderr": None
        }

#
# logging
#
def configure_logging(os_type: OSType) -> logging.Logger:
    logger=logging.getLogger("padsi-agent")
    logger.setLevel(logging.DEBUG)
    formatter=logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")

    match os_type:
        case OSType.LINUX:
            handler=logging.handlers.SysLogHandler(address="/dev/log", facility=logging.handlers.SysLogHandler.LOG_USER)
        case OSType.WINDOWS:
            handler=logging.FileHandler(r"C:\Windows\Temp\padsi-agent.log")
            handler.setFormatter(formatter)

            # To use Windows's own event handler, we need https://github.com/mhammond/pywin32
            #handler=logging.handlers.NTEventLogHandler("padsi-agent")
        case _:
            raise Exception(f"Unhandled OS type {os_type}")

    logger.addHandler(handler)
    return logger

#
# Misc.
#

class PadsiAgent(web.Application):
    """PADSI agent object (multi OS):
    - mounts the virtiofs named "padsi_mgmt" to the appropriate mount point (depending on the OS type)
    - run the initialization scripts as defined by the VM's configuration
    - handles all the requests from the user via an HTTP REST API (as the user if the OS supports it)
    """
    def __init__(self):
        super().__init__()
        self._lock=asyncio.Lock()
        self._os_type=OSType(platform.system())
        self._running_tasks:dict[str,Task]={} # list of "long running" tasks

        # these 2 variables are set later because Windows's service manager uses threads
        self._event:asyncio.Event|None=None
        self._loop:asyncio.AbstractEventLoop|None=None

        self._os_agent=None
        self._logger=configure_logging(self._os_type)
        self._home_dir:str|None=None # home directory, like /home/john.doe or C:\Users\john.doe

        # REST HTTP server
        self.add_routes([
            web.post("/shutdown", self._shutdown),
            web.post("/exec", self.exec_post),
            web.get("/exec", self.exec_get),
            web.get("/tasks", self.tasks),
            web.get("/status", self.status),
        ])

    async def _httpd_setup(self):
        self._logger.info("Starting HTTP service")
        runner=web.AppRunner(self, access_log=None)
        await runner.setup()
        site=web.TCPSite(runner, port=admin_port)
        await site.start()
        self._logger.info(f"Listening on port {admin_port}")

    async def _system_setup(self):
        self._logger.info("Starting system setup")

        match self._os_type:
            case OSType.LINUX:
                from linux import LinuxOSAgent
                self._os_agent=LinuxOSAgent(self._logger)
            case OSType.WINDOWS:
                from windows import WindowsOSAgent
                self._os_agent=WindowsOSAgent(self._logger)
            case _:
                raise Exception(f"Unhandled OS type {self._os_type}")
        self._logger.info(f"Config: {self._os_agent.vm_config}")

        if self._os_agent.vm_config.vm_usage=="RUN":
            # determine HOME directory
            self._home_dir=await self._os_agent.home_dir

            # mount all the configured mount points
            for (fsname, mountpoint) in self._os_agent.vm_config.mountpoints.items():
                self._logger.info(f"Mounting {fsname} on {mountpoint}")
                await self.mount_virtiofs(fsname, mountpoint)

        # run start actions in the first "entrypoint.XXX" file found (we keep extensions for Windows)
        self._logger.info("Starting the 'on-boot' program")
        bin_dir=os.path.join(self._os_agent._management_mount_point, "bin")
        for ext in self._os_agent.script_extensions:
            entrypoint=os.path.join(bin_dir, f"on-boot.{ext}")
            if os.path.exists(entrypoint):
                task=Task(f"on-boot.{ext}", entrypoint, [entrypoint], runner=self._os_agent.get_runner(ext))
                try:
                    await self._mark_task_started(task)
                    task.run(self._os_agent)
                    while True:
                        await asyncio.sleep(0.2)
                        if await task.result is not None:
                            # process has finished
                            await self._mark_task_finished(task)
                            break
                    self._logger.info(f"Finished running '{entrypoint}'")
                    break
                except Exception as e:
                    self._logger.error(f"Could not run script '{entrypoint}': {str(e)}")
        self._logger.info("Done system setup")

    async def main_run(self):
        if self._loop is None:
            self._event=asyncio.Event()
            self._loop=asyncio.get_event_loop()
            try:
                await self._system_setup()
                await self._httpd_setup()
                self._logger.info("Waiting for the program termination event")
            except Exception as e:
                self._logger.error(f"Setup failed (now wait to be stopped): {str(e)}")
            await self._event.wait()
            self._logger.info("Internal event set, stopping")

    def stop(self):
        """Request the service to be stopped"""
        if self._loop is not None:
            self._logger.info("Stop requested")
            self._loop.call_soon_threadsafe(self._event.set) # pyright: ignore
            self._logger.info("Stop request sent")

    async def mount_virtiofs(self, fsname:str, mountpoint:str):
        # compute and create the compute real directory to mount the specified mountpoint, as a sub directory
        # of the users's home directory
        p=PurePosixPath(mountpoint)
        if p.is_absolute():
            if fsname=="padsi-agent":
                real_mp=mountpoint
            else:
                raise Exception(f"CODEBUG: mountpoint {mountpoint} should not be an absolute path")
        else:
            if self._home_dir is None:
                raise Exception("CODEBUG: self._home_dir should not be None")
            real_mp=os.path.join(self._home_dir, *p.parts)

        # actual action
        if self._os_agent is None:
            raise Exception("CODEBUG: self._os_agent should not be None")
        self._os_agent.virtio_mount(fsname, mountpoint, real_mp)

    #
    # Tasks management
    #
    async def _get_task_from_id(self, task_id:str) -> Task|None:
        async with self._lock:
            return self._running_tasks.get(task_id)

    async def _mark_task_started(self, task:Task, ensure_unique:bool=False, unique_descr:str|None=None) -> bool:
        """Tell that a task has been started. This allows the server to perform some tasks sequentially or only once.
        Returns True if this function had to wait for a task with the same ID to terminate before returning
        """
        self._logger.info(f"_mark_task_started({task.id})")
        async with self._lock:
            if task.id not in self._running_tasks:
                self._running_tasks[task.id]=task
                return False

        if ensure_unique:
            if unique_descr:
                raise Exception(unique_descr)
            raise Exception(f"Task '{task.descr}' is already running")

        # wait for the task to be finished
        while True:
            await asyncio.sleep(0.5)
            async with self._lock:
                if task.id not in self._running_tasks:
                    self._running_tasks[task.id]=task
                    return True

    async def _mark_task_finished(self, task:Task):
        """Tell that the task is finished
        """
        self._logger.info(f"_mark_task_finished({task.id})")
        async with self._lock:
            if task.id not in self._running_tasks:
                self._logger.warning(f"Codebug: Task {task.id} ({task.descr}) was not found in _running_tasks")
                return

            self._logger.info(f"Finished running task '{task.id}'")
            del self._running_tasks[task.id]

    #
    # REST API implementation
    #
    async def _shutdown(self, request:web.Request) -> web.Response:
        self._logger.info("shutdown requested")
        task=Task("shutdown", "System shutdown")
        to_do=not await self._mark_task_started(task)
        if to_do:
            if self._os_agent is None:
                raise Exception("CODEBUG: self._os_agent should not be None")
            self._os_agent.shutdown()
        return web.Response(text="Ok")

    async def exec_post(self, request:web.Request) -> web.Response:
        # parse arguments
        try:
            data=await request.json()
            args=data.get("args")
            if not isinstance(args, list):
                raise Exception()
            for arg in args:
                if not isinstance(arg, str):
                    raise Exception()
        except Exception as e:
            return web.Response(text=f"Invalid input ({str(e)})", status=400)

        # perform actions
        try:
            self._logger.info(f"program execution requested: {args}")
            task=Task(str(uuid.uuid4()), " ".join(args), args)
            await self._mark_task_started(task)
            if self._os_agent is None:
                raise Exception("CODEBUG: self._os_agent should not be None")
            task.run(self._os_agent)

            # wait 2' max to return a result, or return task ID
            counter=0
            while counter<10:
                counter+=1
                await asyncio.sleep(0.2)
                res=await task.result
                if res is not None:
                    # process has finished
                    await self._mark_task_finished(task)
                    return json_response(res)

            res={
                "task-id": task.id
            }
            return json_response(res)
        except Exception as e:
            return web.Response(text=f"program execution failed: {str(e)}", status=400)

    async def exec_get(self, request:web.Request) -> web.Response:
        # parse arguments
        task_id=request.rel_url.query.get("task-id")
        if not isinstance(task_id, str):
            return web.Response(text="Invalid input", status=400)

        # execute
        try:
            task=await self._get_task_from_id(task_id)
            if task is None:
                return web.Response(text=f"Task {task_id} not found", status=400)

            r=await task.result
            if r is not None:
                # process has finished
                await self._mark_task_finished(task)

            res=r if r is not None else Task.non_finished_result()
            return json_response(res)
        except Exception as e:
            return web.Response(text=f"Error processing request ({str(e)})", status=400)

    async def tasks(self, request:web.Request) -> web.Response:
        async with self._lock:
            return json_response([task_id for (task_id, _) in self._running_tasks.items()])

    async def status(self, request:web.Request) -> web.Response:
        # parse arguments
        context=request.rel_url.query.get("context")
        if not isinstance(context, str):
            return web.Response(text="Invalid input", status=400)

        # execute
        try:
            if self._os_agent is None:
                raise Exception("CODEBUG: self._os_agent should not be None")
            match context:
                case "user-session-opened":
                    res={
                        "user-session-opened": await self._os_agent.user_session_opened
                    }
                case _:
                    raise Exception(f"Invalid input context '{context}'")

            return json_response(res)
        except Exception as e:
            return web.Response(text=f"Error processing request ({str(e)})", status=400)


if __name__=="__main__":
    app=PadsiAgent()
    asyncio.run(app.main_run())
