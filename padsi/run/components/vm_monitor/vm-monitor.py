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

import sys
import syslog
import time

import psutil

import padsi.run

if len(sys.argv)!=6:
    syslog.syslog(syslog.LOG_ERR, f"Error: {__file__} was called with the wrong number of arguments: {sys.argv}")
    sys.exit(1)

img_file=sys.argv[1]
vars_file=sys.argv[2]
infos_file=sys.argv[3]
qemu_pid=int(sys.argv[4])
viewer_pid_arg=sys.argv[5]
viewer_pid=int(viewer_pid_arg) if viewer_pid_arg!="NODISPLAY" else None

vmversion=padsi.run.VMVersion.from_files(img_file, vars_file, infos_file)
try:
    vmversion.set_state(padsi.run.VMState.RUNNING, "VM has been started")
except Exception as e:
    syslog.syslog(syslog.LOG_ERR, f"Could not change the VM state to RUNNING: {str(e)}")
    sys.exit(1)

# "acquire" the process's infos
qemu_proc:psutil.Process|None=None
viewer_proc:psutil.Process|None=None
counter=0
while True:
    time.sleep(1)
    counter+=1
    try:
        qemu_proc=psutil.Process(qemu_pid)
        viewer_proc=None
        if viewer_pid is not None:
            viewer_proc=psutil.Process(viewer_pid)
        break
    except Exception:
        syslog.syslog(syslog.LOG_ERR, f"Could not find either QEMU (PID {qemu_pid}) or the viewer (PID {viewer_pid}) process")
        if counter>10:
            break

if qemu_proc is None:
    msg="VM could not start"
    syslog.syslog(syslog.LOG_ERR, msg)
    vmversion.set_state(padsi.run.VMState.STOPPED, msg)
    if viewer_proc is not None:
        viewer_proc.kill()
    sys.exit(0)

# waiting for either QEMU or the viewer process to terminate
while True:
    time.sleep(1)
    qemu_stopped=False
    if qemu_proc.is_running():
        if viewer_proc is None or viewer_proc.is_running():
            continue

        # ensure we are in a stable state and not in a transient situation
        time.sleep(1)
        if qemu_proc.is_running():
            # user stopped the viewer => discard what has been done
            msg="VM has been discarded"
            syslog.syslog(syslog.LOG_DEBUG, msg)
            vmversion.set_state(padsi.run.VMState.DISCARDED, msg)
            qemu_proc.kill()
            sys.exit(0)
        else:
            qemu_stopped=True
    else:
        qemu_stopped=True

    if qemu_stopped:
        msg="VM has been stopped"
        syslog.syslog(syslog.LOG_DEBUG, msg)
        vmversion.set_state(padsi.run.VMState.STOPPED, msg)
        if viewer_proc is not None:
            viewer_proc.kill()
        sys.exit(0)
