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


import os
import shutil
import signal
import subprocess
import sys
import syslog
import tempfile
import time
import unittest

import psutil

try:
    import nsbubble

    class ProcessInBubble:
        def __init__(self, api: nsbubble.BubbleAPI, bubble: nsbubble.Bubble, args:list[str], exp_status:int,
            extra_env:dict[str, str]|None=None, capabilities:str|None=None,
            stdin_file:str|None=None, stdout_file:str|None=None, stderr_file:str|None=None, restart:bool=False):
            self._bubble=bubble
            self._api=api
            self._args=args
            self._extra_env=extra_env
            self._capabilities=capabilities
            self._expected_status=exp_status
            self._stdin_file=stdin_file
            self._stdout_file=stdout_file
            self._stderr_file=stderr_file
            self._restart=restart
            self._bpid:int|None=None # PID in the bubble
            self._hpid:int|None=None # PID in the host

        @property
        def args(self) -> list[str]:
            return self._args

        @property
        def bubble_pid(self) -> int:
            if self._bpid is None:
                raise Exception("Process not yet started in bubble")
            return self._bpid

        @property
        def expected_status(self) -> int:
            return self._expected_status

        def run(self, ignore_status:bool=False):
            """Run a process and return its PID in the bubble and in the host
            """
            self._bpid=self._api.start_process(self._args, ignore_status=ignore_status,
                child_stdin=self._stdin_file, child_stdout_file=self._stdout_file, child_stderr_file=self._stderr_file,
                extra_env=self._extra_env, capabilities=self._capabilities, restart=self._restart)
            try:
                self._hpid=self._bubble.map_bubble_pid_to_host(self._bpid)
                print(f"{self._args[0]} process started, PID={self._bpid}, host PID={self._hpid}")
            except FileNotFoundError:
                self._hpid=None
                print(f"{self._args[0]} process started, PID={self._bpid}, already terminated")

            if False and self._hpid is not None:
                proc=subprocess.run(["pstree", "-Tpsl", str(self._hpid)], capture_output=True, text=True)
                if proc.returncode!=0:
                    raise Exception(f"Failed to run pstree on {self._hpid}: {proc.stderr}")
                print(f"{proc.stdout}", end="")

        def update_pid(self) -> int|None:
            for proc in self._api.get_processes():
                if proc.get("args")==self._args:
                    self._bpid=proc.get("pid")
                    return self._bpid
            return None

    class BubbleSetup:
        def __init__(self, capabilities:list[str]|None=None, test_dir:str|None=None):
            self._tmpdir=None
            if test_dir is None:
                tmpdir=tempfile.TemporaryDirectory()
                self._test_dir=tmpdir.name
                self._tmpdir=tmpdir
            else:
                self._test_dir=test_dir
                if os.path.exists(self._test_dir):
                    shutil.rmtree(self._test_dir)
                os.makedirs(self._test_dir)
            test_proc=f"{self._test_dir}/init-test.py"
            shutil.copy(__file__, test_proc)
            log_dir=self._test_dir+"/tmp-logs"
            os.makedirs(log_dir)
            features=nsbubble.Features(with_syslog=False, capabilities=capabilities)
            bubble=nsbubble.Bubble(features, self._test_dir)
            bubble.init_prog=os.path.dirname(os.path.dirname(os.path.realpath(__file__)))+"/crates/init/target/release/init"
            bubble.setup()
            print(f"Bubble setup, init PID: {bubble.init_pid}, run dir: {self._test_dir}, init program: {bubble.init_prog}")
            bubble.wait_for_init_started(max_delay=200)

            self._bubble=bubble
            self._api=nsbubble.BubbleAPI(self._test_dir)
            self._api.wait_for_bubble_ready()
            if not self._api.ready:
                raise Exception("API reports bubble is not ready")

        @property
        def test_dir(self) -> str:
            return self._test_dir

        @property
        def bubble(self) -> nsbubble.Bubble:
            return self._bubble

        @property
        def api(self) -> nsbubble.BubbleAPI:
            return self._api

        def __del__(self):
            self._bubble.destroy()
            if self._tmpdir is not None:
                self._tmpdir.cleanup()

        def ensure_process_listed(self, proc:ProcessInBubble):
            for p in self._api.get_processes(include_running=True, include_terminated=True):
                if p.get("pid")==proc.bubble_pid:
                    return
            raise Exception(f"Process {proc.args} not listed in api.get_processes()")

        def ensure_process_not_listed(self, proc:ProcessInBubble):
            for p in self._api.get_processes(include_running=True, include_terminated=True):
                if p.get("pid")==proc.bubble_pid:
                    raise Exception(f"Process {proc.args} is listed in api.get_processes()")

        def check_process_exit_status(self, proc:ProcessInBubble):
            counter=0
            while True:
                status=self._api.get_process_status(proc.bubble_pid)
                if status is None:
                    counter+=1
                    if counter>60:
                        raise Exception(f"Failed to catch the termination of process {proc.args}")
                    time.sleep(0.02)
                else:
                    if status!=proc.expected_status:
                        raise Exception(f"Invalid reported expected status for process {proc.args}: got {status}, expected {proc.expected_status}")
                    return

    class InitTest(unittest.TestCase):
        def test_inexistant_proc_status(self):
            setup=BubbleSetup()
            try:
                setup.api.get_process_exit_status(1)
            except Exception:
                return
            raise Exception("Should have got a 'process not found' exception")

        def test_bubble_env(self):
            setup=BubbleSetup()
            env=setup.api.environment
            if "FOO" in env:
                raise Exception("FOO should not be present by default in the bubble's environment variables")

            setup.api.declare_env_variable("FOO", "BAR")
            env=setup.api.environment
            if env.get("FOO")!="BAR":
                raise Exception(f"Wrong value '{env.get('FOO')}' for variable FOO")

            setup.api.declare_env_variable("FOO", "")
            env=setup.api.environment
            if env.get("FOO")!="":
                raise Exception(f"Wrong value '{env.get('FOO')}' for variable FOO")

            setup.api.declare_env_variable("FOO", None)
            env=setup.api.environment
            if env.get("FOO") is not None:
                raise Exception("FOO should not be present in the bubble's environment variables")

        def test_proc_env(self):
            setup=BubbleSetup()
            p1=ProcessInBubble(setup.api, setup.bubble, ["sleep", "10"], 0)
            p1.run()
            setup.api.declare_env_variable("FOO", "BAR")
            p2=ProcessInBubble(setup.api, setup.bubble, ["sleep", "11"], 0)
            p3=ProcessInBubble(setup.api, setup.bubble, ["sleep", "12"], 0, extra_env={
                "FOO2": "BAR2"
            })
            p2.run()
            p3.run()

            proc=psutil.Process(setup.bubble.map_bubble_pid_to_host(p1.bubble_pid))
            env=proc.environ()
            if "FOO" in env or "FOO2" in env:
                raise Exception(f"FOO or FOO2 environment variables should not be set (environment: {env})")

            proc=psutil.Process(setup.bubble.map_bubble_pid_to_host(p2.bubble_pid))
            env=proc.environ()
            if "FOO" not in env:
                raise Exception(f"FOO environment variable should be set (environment: {env})")
            if "FOO2" in env:
                raise Exception(f"FOO2 environment variable should not be set (environment: {env})")

            proc=psutil.Process(setup.bubble.map_bubble_pid_to_host(p3.bubble_pid))
            env=proc.environ()
            if "FOO" not in env:
                raise Exception(f"FOO environment variable should be set (environment: {env})")
            if "FOO2" not in env:
                raise Exception(f"FOO2 environment variable should be set (environment: {env})")

        def test_procs(self):
            setup=BubbleSetup()

            p1=ProcessInBubble(setup.api, setup.bubble, ["false"], 1)
            p2=ProcessInBubble(setup.api, setup.bubble, ["sleep", "0.5"], 0)
            procs=[p1, p2]
            for proc in procs:
                proc.run()

            for proc in setup.api.get_processes(include_running=True, include_terminated=True):
                if "pid" not in proc:
                    raise Exception("get_processes() does not include the 'pid' attribute")
                if "args" not in proc:
                    raise Exception("get_processes() does not include the 'args' attribute")
                if "state" not in proc:
                    raise Exception("get_processes() does not include the 'state' attribute")

            for proc in procs:
                setup.ensure_process_listed(proc)
                setup.check_process_exit_status(proc)
                setup.ensure_process_not_listed(proc)

        def test_capabilities(self):
            setup=BubbleSetup(capabilities=["net_bind_service"], test_dir="/tmp/init-test")

            # check capabilities are not set if not needed
            proc=ProcessInBubble(setup.api, setup.bubble, ["cat", "/proc/self/status"], 0, stdout_file="/bubble/run/out")
            proc.run()
            with open(os.path.join(setup.test_dir, "out"), "rt") as fd:
                for line in fd.readlines():
                    if line.startswith("Cap"):
                        (captype, capvalue)=line.split()
                        if captype in ("CapInh", "CapPrm", "CapEff", "CapAmb"):
                            if capvalue!="0000000000000000":
                                raise Exception(f"Unexpected capability {capvalue} for {captype[:-1]}")

            # try to run a process requesting too many capabalities
            proc=ProcessInBubble(setup.api, setup.bubble, ["cat", "/proc/self/status"], 0,
                capabilities="net_bind_service,net_admin", stdout_file="/bubble/run/out")
            try:
                proc.run()
                raise Exception("Process should not have been allowed to run with requested capabilities")
            except Exception:
                pass

            # check capabilities are set up properly
            proc=ProcessInBubble(setup.api, setup.bubble, ["cat", "/proc/self/status"], 0,
                capabilities="net_bind_service", stdout_file="/bubble/run/out")
            proc.run()
            with open(os.path.join(setup.test_dir, "out"), "rt") as fd:
                for line in fd.readlines():
                    if line.startswith("Cap"):
                        (captype, capvalue)=line.split()
                        if capvalue!="0000000000000400":
                            raise Exception(f"Unexpected capability {capvalue} for {captype[:-1]}")

        def test_reaper(self):
            setup=BubbleSetup()
            proc=ProcessInBubble(setup.api, setup.bubble, ["/bubble/run/init-test.py", "sub-sleep"], 0,
                stdout_file="/bubble/run/out")
            proc.run()

            setup.ensure_process_listed(proc)
            time.sleep(0.2)
            setup.check_process_exit_status(proc)
            setup.ensure_process_not_listed(proc)

            # check the subprocess's lifecycle
            with open(os.path.join(setup.test_dir, "out"), "rt") as fd:
                pid=int(fd.read().strip())
                hpid=setup.bubble.map_bubble_pid_to_host(pid)
                init_pid=setup.bubble.map_bubble_pid_to_host(1)
                psproc=psutil.Process(hpid)
                if psproc.ppid()!=init_pid:
                    raise Exception("Subprocess's parent is not the init process")

                psproc.kill()
                try:
                    psproc=psutil.Process(hpid)
                    raise Exception("Subprocess is still present")
                except Exception:
                    pass

        def test_stop(self):
            setup=BubbleSetup()
            proc=ProcessInBubble(setup.api, setup.bubble, ["sleep", "60"], 128+9)
            proc.run()

            setup.api.stop_process(proc.bubble_pid)
            setup.check_process_exit_status(proc)

        def test_killed(self):
            setup=BubbleSetup()
            proc=ProcessInBubble(setup.api, setup.bubble, ["sleep", "60"], 128+9)
            proc.run()

            # send the SIGKILL (9) to process
            hpid=setup.bubble.map_bubble_pid_to_host(proc.bubble_pid)
            if hpid is None:
                raise Exception("Could not get process's PID in the 'init' namespace")
            os.kill(hpid, signal.SIGKILL)

            setup.check_process_exit_status(proc)

        def test_suspend(self):
            setup=BubbleSetup()
            proc=ProcessInBubble(setup.api, setup.bubble, ["sleep", "60"], 128+9)
            proc.run()

            hpid=setup.bubble.map_bubble_pid_to_host(proc.bubble_pid)
            if hpid is None:
                raise Exception("Could not get process's PID in the 'init' namespace")
            psproc=psutil.Process(hpid)
            if psproc.status()!=psutil.STATUS_SLEEPING:
                raise Exception("Process should be in SLEEPING state")

            setup.api.suspend_process(proc.bubble_pid)
            if psproc.status()!=psutil.STATUS_STOPPED:
                raise Exception("Process should be in STOPPED state")

            setup.api.resume_process(proc.bubble_pid)
            st=psproc.status()
            if st!=psutil.STATUS_SLEEPING:
                raise Exception(f"Process should be in SLEEPING state (is {st})")

        def test_restart(self):
            setup=BubbleSetup()

            proc=ProcessInBubble(setup.api, setup.bubble, ["sleep", "0.05"], 0, restart=True)
            proc.run()
            setup.ensure_process_listed(proc)
            pid=proc.bubble_pid
            time.sleep(0.1)
            pid2=proc.update_pid()
            if pid2 is None:
                raise Exception("Process was not restarted")
            if pid==pid2:
                raise Exception("Testing code bug: 'sleep' process should have stopped")
            time.sleep(0.5)
            pid2=proc.update_pid()
            if pid2 is not None:
                raise Exception("Process has been restarted even though restart rate should be exceeded")

        def test_property(self):
            setup=BubbleSetup()
            prop=setup.api.auto_stop
            if prop:
                raise Exception(f"auto-stop should be False, not {prop}")

            setup.api.auto_stop=True
            prop=setup.api.auto_stop
            if not prop:
                raise Exception(f"auto-stop should be True, not {prop}")

        def test_stdio(self):
            setup=BubbleSetup()

            data="passed via stdin"

            # stdin without a file
            proc=ProcessInBubble(setup.api, setup.bubble, ["/bubble/run/init-test.py", "cat"], 0,
                stdin_file=data, stdout_file="/bubble/run/stdout")
            proc.run()
            setup.check_process_exit_status(proc)

            fname_out=os.path.join(setup.test_dir, "stdout")
            with open(fname_out, "rt") as fd:
                out=fd.read()
                if out!=data:
                    raise Exception(f"Did not get expected output '{data}', got '{out}'")

            # stdout
            fname_in=os.path.join(setup.test_dir, "stdin")
            with open(fname_in, "wt") as fd:
                fd.write(data)

            # stdout
            proc=ProcessInBubble(setup.api, setup.bubble, ["/bubble/run/init-test.py", "cat"], 0,
                stdin_file="/bubble/run/stdin", stdout_file="/bubble/run/stdout")
            proc.run()
            setup.check_process_exit_status(proc)

            fname_out=os.path.join(setup.test_dir, "stdout")
            with open(fname_out, "rt") as fd:
                out=fd.read()
                if out!=data:
                    raise Exception(f"Did not get expected output '{data}', got '{out}'")

            # stderr
            proc=ProcessInBubble(setup.api, setup.bubble, ["/bubble/run/init-test.py", "caterr"], 0,
                stdin_file="/bubble/run/stdin", stderr_file="/bubble/run/stderr")
            proc.run()
            setup.check_process_exit_status(proc)

            fname_err=os.path.join(setup.test_dir, "stderr")
            with open(fname_err, "rt") as fd:
                out=fd.read()
                if out!=data:
                    raise Exception(f"Did not get expected stderr '{data}', got '{out}'")

except Exception:
    if len(sys.argv)==1:
        print("Failed to run test, is the PYTHONPATH correctly set?", file=sys.stderr)
        sys.exit(1)
    pass

if __name__=='__main__':
    if len(sys.argv)>1:
        try:
            match sys.argv[1]:
                case "sub-sleep":
                    proc=subprocess.Popen(["sleep", "60"])
                    print(f"{proc.pid}")
                    sys.exit(0)
                case "cat":
                    data=sys.stdin.read()
                    sys.stdout.write(data)
                    sys.exit(0)
                case "caterr":
                    data=sys.stdin.read()
                    sys.stderr.write(data)
                    sys.exit(0)
        except Exception as e:
            syslog.syslog(syslog.LOG_ERR, f"Error: {str(e)}")
            raise e

    # fall back to normal behaviour
    unittest.main()
