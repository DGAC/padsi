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
import tempfile
import unittest

import padsi.run.vm.db as db

_db_schema={
    1: [
        "CREATE TABLE t0 (id INTEGER NOT NULL, name STRING)"
    ],
}

class DBTest(unittest.TestCase):
    def test_mode(self):
        testdir=tempfile.TemporaryDirectory()
        try:
            # DB created in a directory with write premissions
            dbdir=os.path.join(testdir.name, "DB1")
            os.makedirs(dbdir)
            os.chmod(dbdir, 0o755)
            dbo=db.Sqlite3DB(os.path.join(dbdir, "thedb"), _db_schema)
            self.assertFalse(dbo.read_only)
            dbo=db.Sqlite3DB(os.path.join(dbdir, "thedb"), _db_schema, read_only=True)
            self.assertTrue(dbo.read_only)

            # try to create a DB in a directory without write permission
            dbdir=os.path.join(testdir.name, "DB2")
            os.makedirs(dbdir)
            os.chmod(dbdir, 0o555)
            self.assertRaises(Exception, db.Sqlite3DB, os.path.join(dbdir, "thedb"), _db_schema)
            self.assertRaises(Exception, db.Sqlite3DB, os.path.join(dbdir, "thedb"), _db_schema, read_only=True)

            # open a DB in a directory with write permissions
            dbdir=os.path.join(testdir.name, "DB3")
            os.makedirs(dbdir)
            os.chmod(dbdir, 0o755)
            dbo=db.Sqlite3DB(os.path.join(dbdir, "thedb"), _db_schema)
            os.chmod(dbdir, 0o555)
            dbo=db.Sqlite3DB(os.path.join(dbdir, "thedb"), _db_schema)
            self.assertTrue(dbo.read_only)

            # open a read-only DB
            dbdir=os.path.join(testdir.name, "DB4")
            os.makedirs(dbdir)
            os.chmod(dbdir, 0o755)
            dbo=db.Sqlite3DB(os.path.join(dbdir, "thedb"), _db_schema)
            for fname in dbo.files:
                os.chmod(fname, 0o444)
            dbo=db.Sqlite3DB(os.path.join(dbdir, "thedb"), _db_schema)
            self.assertTrue(dbo.read_only)
        finally:
            testdir.cleanup()

    def test_context(self):
        testdir=tempfile.TemporaryDirectory()
        try:
            with db.Sqlite3DB(os.path.join(testdir.name, "thedb"), _db_schema) as dbo:
                self.assertFalse(dbo.is_closed)
                self.assertEqual(len(dbo.files), 3) # DB file, DB-wal, DB-shm
            self.assertTrue(dbo.is_closed)
            self.assertEqual(len(dbo.files), 1) # DB file only
        finally:
            testdir.cleanup()
    

if __name__=='__main__':
    unittest.main()