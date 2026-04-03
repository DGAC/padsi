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


# Server which gets "Web site blocked" events via a Unix socket (/run/user/<UID>/padsi-notify.sock) and displays a UI notification
# optionaly allowing the user to open the Web site in a zone wher it is allowed
# incoming events are JSON strings like:
# {
#    "url": "https://www.example.com/",
#    "browser": "firefox",
# }

import json
import os
import socket
import struct
import sys
import syslog
import urllib.parse
from dataclasses import dataclass

import dbus
import dbus.mainloop.glib
import requests
import requests_unixsocket
from gi.repository import GLib  # pyright: ignore

import padsi.config


@dataclass
class Notification:
    nid: int        # notification ID
    url: str        # URL of the blocked site
    browser: str    # brower to use
    zones: list[str]# list of zones which can be used

class NotificationsServer:
    def __init__(self, gconf:padsi.config.Configuration, user_session_dir:str):
        self._gconf=gconf
        self._notifs:dict[int,Notification]={} # key=notification ID
        self._buffers:dict[int,bytes]={} # data buffer per FD

        self._user_session_dir=user_session_dir
        self._socket_path=os.path.join(user_session_dir, "padsi-notify.sock")
        self._socket:socket.socket|None=None

        self._bus:dbus.SessionBus|None=None
        self._notify_iface:dbus.Interface|None=None

    def setup(self):
        # Set up DBus main loop
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)

        # Connect to DBus notification service
        self._bus=dbus.SessionBus()
        notify_obj=self._bus.get_object("org.freedesktop.Notifications", "/org/freedesktop/Notifications")
        self._notify_iface=dbus.Interface(notify_obj, "org.freedesktop.Notifications")

        self._bus.add_signal_receiver(self._on_notif_clicked,
                                      dbus_interface="org.freedesktop.Notifications",
                                      signal_name="ActionInvoked")

        self._bus.add_signal_receiver(self._on_notif_closed,
                                      dbus_interface="org.freedesktop.Notifications",
                                      signal_name="NotificationClosed")

    def _on_notif_clicked(self, nid:dbus.UInt32, action_key:dbus.String):
        nid=int(nid)
        if nid in self._notifs:
            notif=self._notifs.pop(nid)
            action_key=str(action_key)
            if action_key.startswith("R-"):
                zone=action_key[2:]
                if zone not in notif.zones:
                    syslog.syslog(syslog.LOG_ERR, f"CODEBUG: trying to open URL in unavailable zone '{zone}'")
                else:
                    syslog.syslog(syslog.LOG_INFO, f"asking to open '{notif.url}' with {notif.browser} in zone '{zone}'")
                    self._open_url_in_zone(zone, notif.url, notif.browser)

    def _on_notif_closed(self, nid:dbus.UInt32, reason:dbus.UInt32):
        nid=int(nid)
        if nid in self._notifs:
            self._notifs.pop(nid)

    def _on_new_connection(self, source, condition):
        # accept the connection
        (conn, _)=self._socket.accept() # pyright: ignore
        conn.setblocking(False)

        # prepare reading received data
        fd=conn.fileno()
        self._buffers[fd]=b""
        GLib.io_add_watch(conn, GLib.IO_IN, self._on_data_received)
        return True

    # Handle incoming data from client (return False to remove the GLib watch)
    def _on_data_received(self, conn, condition):
        fd=conn.fileno()
        try:
            # get available data
            data=conn.recv(4096)
            if not data:
                conn.close()
                return False
            self._buffers[fd]+=data

            # try to parse complete message
            try:
                msg=json.loads(self._buffers[fd].decode())
                url=msg["url"]
                browser=msg["browser"]
                if browser not in padsi.config.ProgramPoliciesFactory().supported_browsers:
                    browser=padsi.config.ProgramPoliciesFactory().default_browser

                purl=urllib.parse.urlparse(url)
                if purl.scheme not in ("http", "https"):
                    raise Exception(f"Unsuported scheme '{purl.scheme}'")

                port=purl.port
                if port is None:
                    port=80 if purl.scheme=="http" else 443

                # get the PID and the zone of the connected process
                ucred=conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
                (pid,uid,gid)=struct.unpack("3i", ucred)
                zones=self._get_usable_zones(pid, purl.hostname, port)

                furl=purl.netloc.replace(".", "\u200B.") # insert invisible spaces so GNOME Shell does not make it clickable

                fscheme="\U0001f512 " if purl.scheme=="https" else f"{purl.scheme}://"

                actions:list[str]=[]
                for zone in zones:
                    actions+=[f"R-{zone}", f"Open in the '{zone}' zone"]
                # actions+=["other", "..."] for later to open a menu with more options

                if len(actions)>0:
                    # display notification
                    # refer to https://specifications.freedesktop.org/notification-spec/1.3/protocol.html
                    if self._notify_iface is None:
                        raise Exception("CODEBUG: self._notify_iface should not be None")
                    nid=self._notify_iface.Notify(
                        "PADSI", # app_name
                        0, # replaces_id
                        "", # icon
                        "Web site blocked", # summary
                        f"Acess to <b>{fscheme}{furl}</b> is blocked", # body
                        actions, # actions
                        {"image-path": os.path.join(os.path.realpath(os.path.dirname(__file__)), "webroot", "padsi.png")}, # hints
                        10 # expiration timeout (-1 = default)
                    )
                    nid=int(nid)
                    self._notifs[nid]=Notification(nid, url, browser, zones)
                else:
                    syslog.syslog(syslog.LOG_DEBUG, f"{fscheme}{furl} is not allowed to be opened in any zone")

                conn.sendall(b"OK")
                conn.close()
                del self._buffers[fd]
                return False

            except json.JSONDecodeError:
                return True # wait for more data

        except Exception as e:
            print()
            conn.sendall(f"ERROR: {str(e)}".encode())
            conn.close()
            del self._buffers[fd]
            return False

    def _get_usable_zones(self, pid:int, host:str, port:int) -> list[str]:
        """List all the zones where the site is allowed"""
        user_service_socket_path=os.path.join(self._user_session_dir, "padsi-userv.sock")
        q_socket=urllib.parse.quote_plus(user_service_socket_path)
        session=requests_unixsocket.Session()
        res:list[str]=[]
        try:
            params={
                "host": host,
                "port": port,
                "pid": pid
            }
            resp=session.get(f"http+unix://{q_socket}/web-redir", params=params, timeout=5)

            if resp.ok:
                data=resp.json()
                exp=data.get("exception")
                if exp is not None:
                    syslog.syslog(syslog.LOG_ERR, f"User service for GET /web-redir returned an error: {exp}")
                else:
                    res=data["zones"]
            else:
                syslog.syslog(syslog.LOG_ERR, f"User service error for GET /web-redir: {resp.text}")
        except requests.exceptions.ConnectionError as e:
            syslog.syslog(syslog.LOG_ERR, f"User service connection refused: {str(e)}")
        return res

    def _open_url_in_zone(self, zone:str, url:str, browser:str):
        user_service_socket_path=os.path.join(self._user_session_dir, "padsi-userv.sock")
        q_socket=urllib.parse.quote_plus(user_service_socket_path)
        session=requests_unixsocket.Session()
        try:
            data={
                "zone": zone,
                "url": url,
                "browser": browser
            }
            resp=session.post(f"http+unix://{q_socket}/web-redir", data=json.dumps(data), headers={"Content-Type": "application/json"},
                              timeout=5)
            if resp.ok:
                data=resp.json()
                exp=data.get("exception")
                if exp is not None:
                    syslog.syslog(syslog.LOG_ERR, f"User service for GET /web-redir returned an error: {exp}")
            else:
                syslog.syslog(syslog.LOG_ERR, f"User service error for GET /web-redir: {resp.text}")
        except requests.exceptions.ConnectionError as e:
            syslog.syslog(syslog.LOG_ERR, f"User service connection refused: {str(e)}")

    def run(self):
        if os.path.exists(self._socket_path):
            os.remove(self._socket_path)

        self._socket=socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._socket.bind(self._socket_path)
        os.chmod(self._socket_path, 0o666)
        self._socket.listen(5)
        self._socket.setblocking(False)

        GLib.io_add_watch(self._socket, GLib.IO_IN, self._on_new_connection)

        dbus_loop=GLib.MainLoop()
        dbus_loop.run()

if __name__=="__main__":
    try:
        if len(sys.argv)!=3:
            raise Exception(f"Usage: {sys.argv[0]} <user session directory> <configuration directory>")
        user_session_dir=sys.argv[1]
        config_dir=sys.argv[2]

        gconf=padsi.config.Configuration(config_dir)

        uid=os.geteuid()
        os.environ["DBUS_SESSION_BUS_ADDRESS"]=f"unix:path=/run/user/{os.geteuid()}/bus" # needed to connect to the DBus session bus
        server=NotificationsServer(gconf, user_session_dir)
        server.setup()
        server.run()
    except Exception as e:
        syslog.syslog(syslog.LOG_ERR, f"Error: {str(e)}")
        print(str(e), file=sys.stderr)
        sys.exit(1)
