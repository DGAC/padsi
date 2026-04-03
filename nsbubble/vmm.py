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

from __future__ import annotations

import json
import socket


class QMP:
    """Allows to use QEMU's Machine Protocol to manage the VM "from the outside"
    Cf. https://qemu-project.gitlab.io/qemu/interop/qemu-qmp-ref.html
    """
    def __init__(self):
        self._socket_file="/tmp/monitor.sock"
        self._client=socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._client.connect(self._socket_file)
        self._ensure_qmp_nego_done()

    def _recv(self) -> list[str]:
        self._client.setblocking(True)
        data=self._client.recv(1024)
        self._client.setblocking(False)
        while True:
            try:
                resp=self._client.recv(1024)
                data=data+resp
            except BlockingIOError:
                le=data.decode().split("\r\n")
                if le[-1]=="":
                    le.pop()
                print(f"RECV: {json.dumps(le, indent=4)}")
                return le

    def _ensure_qmp_nego_done(self):
        resp=self._recv()[0]
        self._serverinfo=json.loads(resp)
        #print(f"SERVER: {json.dumps(self._serverinfo, indent=4)}")
        self._send_command("qmp_capabilities", { "enable": ["oob"] })

    def _send_command(self, cmde:str, arguments:dict|None=None):
        """Send a command to the QMP socket, and return the result"""
        cmde={ # pyright: ignore
            "execute": cmde,
            "arguments": arguments if arguments is not None else {}
        }
        self._client.sendall(json.dumps(cmde).encode())

        retval=None
        for entry in self._recv():
            try:
                data=json.loads(entry)
                if "event" in data:
                    print(f"Got EVENT {data['event']} --> {data['data']}")
                else:
                    if "return" in data:
                        if retval:
                            print(f"??? Multiple possible retval, previous: {retval}")
                        retval=data["return"]
                    elif "error" in data:
                        retval=Exception(data["error"]["desc"])
            except Exception:
                print(f"??? exception for: {entry}")
        if isinstance(retval, Exception):
            raise retval
        return retval

    def _list_removable_blockdevs(self) -> list[str]:
        info=self._send_command("query-block")
        res=[]
        if info is not None:
            for blkdev in info:
                if blkdev.get("removable") and blkdev.get("qdev"):
                    res.append(blkdev.get("qdev"))
        return res

    def execute_command(self, cmde:str, arguments:dict|None=None):
        """Usefull command in the debug mode"""
        resp=self._send_command(cmde, arguments)
        print(f"==> {json.dumps(resp, indent=4)}")

    def set_cdrom_iso(self, path:str|None):
        """Change the ISO image attached to a CDROM drive.
        Pass None to eject any existing one
        """
        # see also QemuDiskHotplug: https://wiki.ubuntu.com/QemuDiskHotplug
        rem_blkdev_list=self._list_removable_blockdevs()
        if len(rem_blkdev_list)==0:
            raise Exception("Could not identify any remobavle block device")
        if len(rem_blkdev_list)>1:
            raise Exception(f"TODO: more than one removable block device: {rem_blkdev_list}")
        qdev=rem_blkdev_list[0]
        if path is None:
            self._send_command("eject", {"id": qdev})
        else:
            self._send_command("blockdev-change-medium", {"id": qdev, "filename": path, "read-only-mode": "read-only", "force": True})

    def shutdown(self):
        """Shut the system down"""
        # does not work properly, the OS can ignore the powerdown request
        self._send_command("system_powerdown")

    def run_command(self, cmde:str):
        """Run a command in the OS, no feed back though...
        """
        alpha_map={
            "a": "q",
            "z": "w",
            "e": "e",
            "r": "r",
            "t": "t",
            "y": "y",
            "u": "u",
            "i": "i",
            "o": "o",
            "p": "p",
            "q": "a",
            "s": "s",
            "d": "d",
            "f": "f",
            "g": "g",
            "h": "h",
            "j": "j",
            "k": "k",
            "l": "l",
            "m": "semicolon",
            "w": "z",
            "x": "x",
            "c": "c",
            "v": "v",
            "b": "b",
            "n": "n",
        }
        map={}
        for c, m in alpha_map.items():
            map[c]=m
            map[c.upper()]=f"shift-{m}"

        map.update({
            "/": "slash",
            "-": "minus",
            "+": "kp_add",
            "=": "equal",
            "\\": "backspace",
            "[]": "bracket_left",
            "]": "bracket_right",
            "'": "apostrophe",
            ",": "comma",
            "*": "asterisk",
            ".": "dot",
            " ": "spc",
            "1": "kp_1",
            "2": "kp_2",
            "3": "kp_3",
            "4": "kp_4",
            "5": "kp_5",
            "6": "kp_6",
            "7": "kp_7",
            "8": "kp_8",
            "9": "kp_9",
            "0": "kp_0"
        })

        # convert to keycodes
        keycodes=[]
        for c in cmde:
            m=map.get(c)
            if m is None:
                keycodes.append(c)
            else:
                keycodes.append(m)

        # Windows prefix
        keysargs=[
            {"type": "qcode", "data": "meta_l"},
            {"type": "qcode", "data": "r"}
        ]
        self._send_command("send-key", {"keys": keysargs})

        # remove anythin already present
        keysargs=[
            {"type": "qcode", "data": "backspace"}
        ]
        self._send_command("send-key", {"keys": keysargs})

        # send command char after char
        for c in keycodes:
            keysargs=[]
            for p in c.split("-"):
                keysargs+=[
                    {"type": "qcode", "data": p}
                ]
            print(f"{c} ==> {keysargs}")
            self._send_command("send-key", {"keys": keysargs})

        # return
        keysargs=[
            {"type": "qcode", "data": "ret"}
        ]
        self._send_command("send-key", {"keys": keysargs})
