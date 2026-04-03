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
import os
import unittest

import psutil

from padsi.misc import UserSessionNotifier


class TestNotifier(UserSessionNotifier):
    def __init__(self):
        super().__init__()
        self._logged_in_users:set[int]=set()

    def user_logged_in_cb(self, uid:int, gid:int, shell_proc:psutil.Process):
        print(f"User {uid=} {gid=} is logged in")
        self._logged_in_users.add(uid)

    def user_logged_out_cb(self, uid:int, gid:int):
        print(f"User {uid=} {gid=} is logged out")
        self._logged_in_users.remove(uid)

    def user_is_logged_in(self, uid:int) -> bool:
        return uid in self._logged_in_users

class MainTest(unittest.TestCase):
    def test_login(self):
        async def run_test():
            n=TestNotifier()
            asyncio.create_task(n.run())
            await asyncio.sleep(0.5)
            self.assertEqual(n.user_is_logged_in(os.geteuid()), True)
        asyncio.run(run_test())

if __name__=='__main__':
    unittest.main()
