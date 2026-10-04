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
import syslog
from dataclasses import dataclass

import psutil


@dataclass
class _UserSession:
    uid:int
    gid:int
    shell_pid:int

class UserSessionNotifier:
    """Watches when users open and close graphical (GNOME shell for now) sessions
    and call a callback when it happens. Needs to be sub-classed
    """
    def __init__(self):
        self._sessions:dict[int,_UserSession]={}

    def user_logged_in_cb(self, uid:int, gid:int, shell_proc:psutil.Process):
        """Function called when a user has logged in
        """

    def user_logged_out_cb(self, uid:int):
        """Function called when a user has logged out
        """

    async def run(self):
        p1=psutil.Process(1) # system's init process
        while True:
            try:
                try:
                    # get the list of all GNOME shell processes (each indicating a user with an opened session)
                    all_shell_pids:list[int]=[]
                    for systemd_proc in [p for p in p1.children() if p.name()=="systemd"]:
                        shell_procs=[p for p in systemd_proc.children() if p.name()=="gnome-shell"]
                        if len(shell_procs)>0:
                            if len(shell_procs)>1:
                                syslog.syslog(syslog.LOG_WARNING, "User's systemd has more than one gnome-shell processes!")
                            shell_proc=shell_procs[0]
                            all_shell_pids.append(shell_proc.pid)
                            if shell_proc.pid not in self._sessions:
                                try:
                                    uid=shell_proc.uids().real
                                    if uid>=1000:
                                        # this is a real user
                                        session=_UserSession(uid, shell_proc.gids().real, shell_proc.pid)
                                        self._sessions[session.shell_pid]=session
                                        self.user_logged_in_cb(session.uid, session.gid, shell_proc)
                                except Exception as e: # noqa: BLE001
                                    syslog.syslog(syslog.LOG_ERR, f"Error handling user {shell_proc.uids().real} logged: {e}")

                    # handle users which have logged out
                    for pid in [pid for pid in self._sessions if pid not in all_shell_pids]:
                        try:
                            session=self._sessions.get(pid)
                            if session is not None:
                                self.user_logged_out_cb(session.uid)
                                del self._sessions[pid]

                        except Exception as e: # noqa: BLE001
                            syslog.syslog(syslog.LOG_ERR, f"Error handling user's shell {pid} logged out: {e}")

                except psutil.ZombieProcess:
                    pass # come back later, transient state
                except psutil.AccessDenied:
                    syslog.syslog(syslog.LOG_ERR, "Error handling user logged or unlogged: process list access denied")
                except Exception as e: # noqa: BLE001
                    syslog.syslog(syslog.LOG_ERR, f"Error handling user logged or unlogged: {e}")
            except asyncio.exceptions.IncompleteReadError:
                break

            try:
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                return
