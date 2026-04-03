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


#
# Implementation of a base SQLite DB object, intended to be inherited by all objects which access SQLite database files.
# It handles:
# - schema creation and updates, with versioning (held in the 'dbinfo' table, see the dbinfo_table_name variable)
# - transactions (incl. nested ones)
# - retries in case the database is locked at some point.
#

import logging
import os
import re
import sqlite3
import sys
import time

import psutil

"""
Number of seconds to wait when a database is locked before actually raising an error
"""
database_locked_timeout=30

"""Name of the internal table used to store schema versioning.
This table can also be used to store other values mapped on keys"""
dbinfo_table_name="dbinfo"

def _lsof_list(filename:str, only_write_processes:bool=True) -> list[psutil.Process]:
    """List processes having opened the specified file
    If the with_write argument is True, then only the processes which have opened the file in a write
    mode will be listed
    """
    res:list[psutil.Process]=[]
    for pid in psutil.pids():
        try:
            proc=psutil.Process(pid)
            for (of_path, _of_fd) in proc.open_files():
                if (not only_write_processes or only_write_processes) and os.path.samefile(filename, of_path):
                    res.append(proc)
        except (PermissionError, psutil.AccessDenied):
            pass
        except Exception as e:
            print(f"LSOF for PID error: {str(e)}", file=sys.stderr)
            raise e
    return res

def _lsof_any(filename: str, only_write_processes:bool=True) -> bool:
    """Tell if the specified file is opened by any process.
    If the with_write argument is True, then only the processes which have opened the file in a write
    mode will be considered
    """
    for pid in psutil.pids():
        try:
            proc=psutil.Process(pid)
            for (of_path, _of_fd) in proc.open_files():
                if (not only_write_processes or only_write_processes) and os.path.samefile(filename, of_path):
                    return True
        except (PermissionError, psutil.AccessDenied):
            pass
        except Exception as e:
            print(f"LSOF for PID error: {str(e)}", file=sys.stderr)
            raise e
    return False

def regexp(y, x, search=re.search):
    return 1 if search(y, x) else 0

class Sqlite3DB:
    """Database access"""
    def __init__(self, db_file:str, schema_definition:dict|None=None, read_only:bool=False, use_wal:bool=True):
        """Opens a connection to the database file @db_file
        NB:
          - the DB file is created if it does not already exist (and if read_only is not specified)
          - the same DB can be opened from multiple processes
          - if use_wal is True, then the SQLite DB will use the WAL mode (https://sqlite.org/wal.html) and opening
            the database, even is read-only mode, will require write access to the directory containing the DB file
            If False, the default is the rollback journal mode (https://sqlite.org/lockingv3.html#rollback)

        If @schema_definition is not None:
          - the special "dbinfo" table holds information about the database schema version (to be incremented each time there is schema update)
          - the initial schema is created if not yet present, from the data in @schema_definition, starting from the first version and applying all
            the modifications brought by each next version

            The schema_definition variable must be a structure like, and _must_ start at an index > 0:
            {
                1: [ # initial schema
                    "CREATE TABLE ...",
                    "CREATE TABLE ...",
                    "CREATE INDEX ..."
                ],
                2: [
                    "CREATE TABLE ...",
                    "SELECT * FROM xxx INTO ...", # data migration
                    "UPDATE ...",
                    "DELETE TABLE ..."
                ]
            }
        """
        if schema_definition and (not isinstance(schema_definition, dict) or 0 in schema_definition):
            raise Exception(f"Invalid schema definition argument '{schema_definition}'")
        self._db_file=os.path.realpath(db_file)
        db_dir=os.path.dirname(self._db_file)
        self._conn=None # actual database connection

        # determine if DB must be opened read-only, and various checks to avoid more difficult to understand errors later
        ro_mode=False
        if os.path.exists(self._db_file):
            if os.access(self._db_file, os.R_OK, effective_ids=True):
                if not os.access(self._db_file, os.W_OK, effective_ids=True):
                    ro_mode=True
            else:
                raise Exception(f"Access denied to DB file '{self._db_file}'")
            if not os.access(db_dir, os.W_OK, effective_ids=True):
                ro_mode=True
        else:
            if read_only:
                raise Exception("Can't open a non existant database in rean-only mode")
            if not os.access(db_dir, os.W_OK, effective_ids=True):
                raise Exception(f"Write access denied to DB directory '{db_dir}'")
        self._ro_mode=read_only or ro_mode

        # actually open connection
        try:
            tmp=self._db_file.replace("?", "%3f").replace("#", "%23") # https://www.sqlite.org/uri.html
            conn=sqlite3.connect(f"file:{tmp}?mode={'ro' if self._ro_mode else 'rwc'}", uri=True)
            conn.create_function("regexp", 2, regexp)
            conn.isolation_level=None
            self._conn=conn
        except Exception as e:
            raise Exception(f"Could not open DB conection to '{self._db_file}': {str(e)}")

        # Misc. init
        self._transaction_level:int=0 # 0 if no transaction started, incremented by 1 each time transaction_begin() is called,
                                      # and decremented by 1 each time transaction_commit() or transaction_rollback() is called.
                                      # it is used as names when nesting transactions base internally on savepoints
        self._transaction_cursor:sqlite3.Cursor|None=None # no transaction started yet

        # set up or update the schema
        if schema_definition is not None and not self._ro_mode:
            if use_wal:
                self.execute("PRAGMA journal_mode=WAL")
            counter=database_locked_timeout
            while counter>0:
                try:
                    self.execute("PRAGMA busy_timeout=0") # deactivate busy handlers
                    self.transaction_begin()
                    # load the schema version from the DB (slower)
                    version=self._get_schema_version()

                    self._update_schema(schema_definition, version)
                    version=self._get_schema_version()

                    if self._transaction_level>0:
                        self.transaction_commit()
                    break
                except sqlite3.OperationalError as e:
                    if self._transaction_level>0:
                        self.transaction_rollback()
                    if str(e)=="database is locked":
                        logging.log(logging.ERROR, f"Database '{self._db_file}' is locked, opened by programs {self._locker_processes_as_string()}")
                        counter-=1
                        time.sleep(0.005) # yield control to something else if possible
                    else:
                        raise e

        try:
            self.execute("PRAGMA busy_timeout=1000") # 1 second
        except Exception as e:
            logging.log(logging.WARNING, f"Failed to execute PRAGMA busy_timeout: {str(e)}")

    # context manager
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def __del__(self):
        self.close()

    def _update_schema(self, schema_definition:dict, current_version:int):
        tversion=max(schema_definition.keys())
        if current_version==tversion:
            return
        try:
            self.transaction_begin()
            self.execute(f"CREATE TABLE IF NOT EXISTS {dbinfo_table_name} (key TEXT NOT NULL, value TEXT NOT NULL)")
            version=None
            for version in schema_definition:
                assert isinstance(version, int)
                if version>current_version:
                    schema=schema_definition[version]
                    for sql in schema:
                        self.execute(sql)
            self.execute(f"DELETE FROM {dbinfo_table_name} WHERE key='version'")
            self.execute(f"INSERT INTO {dbinfo_table_name} (key, value) VALUES ('version', :version)", {"version": version})
            self.transaction_commit()
        except Exception as e:
            self.transaction_rollback()
            raise e

    def _get_schema_version(self) -> int:
        """Get the current database schema version, will always return 0 if the DB's schema has not yet been created.
        NB: the version is incremented with each database schema change
        """
        try:
            return int(self.select_1st_row(f"SELECT value FROM {dbinfo_table_name} WHERE key='version'")[0]) # pyright: ignore
        except Exception:
            return 0

    def get_write_processes(self) -> list[psutil.Process]:
        """Get the list of processes which may lock the database
        """
        return _lsof_list(self._db_file, True)

    def get_all_processes(self) -> list[psutil.Process]:
        """Get the list of processes which use the database
        """
        return _lsof_list(self._db_file, False)

    def is_being_used(self) -> bool:
        """Returns True if at least one process is using the database
        """
        return _lsof_any(self._db_file, False)

    def _locker_processes_as_string(self) -> str:
        """Get the list of processes which may lock the database as a list of
        """
        return ", ".join([' '.join(p.cmdline()) for p in _lsof_list(self._db_file, True)])

    @property
    def schema_version(self) -> int:
        """Get the database schema version, which is incremented with each revision, Returns 0 if the database has not yet been initialized"""
        if self._conn is None:
            raise Exception("Database could not be opened")
        return self._get_schema_version()

    @property
    def read_only(self) -> bool:
        return self._ro_mode

    @property
    def is_closed(self) -> bool:
        return self._conn is None

    def close(self):
        """Close the DB
        """
        if self._conn is not None:
            self._conn.close()
            self._conn=None

    @property
    def files(self) -> list[str]:
        """Get the list of all the files used by the DB"""
        res=[]
        for ext in ("", "-shm", "-wal", "-journal"):
            fname=f"{self._db_file}{ext}"
            if os.path.exists(fname):
                res.append(fname)
        return res

    def list_all_tables(self) -> list[str]:
        """List all tables declared in the database except for the "dbinfo" table which is reserved.
        NB: there is no database ordering in the resulting list
        """
        def _get_all(rows):
            return [row[0] for row in rows]
        sql=f"SELECT name FROM sqlite_master WHERE type='table' AND name!='{dbinfo_table_name}'"
        return self.select_with_func(sql, None, _get_all)

    def transaction_begin(self):
        """Begin a transaction.
        Nested transactions are handled internally using savepoints
        """
        # cf. https://www.sqlite.org/lang_transaction.html and https://www.sqlite.org/lang_savepoint.html
        if self._transaction_level==0:
            # no transaction started yet
            cursor=self._get_cursor(force=True)
            cursor.execute("BEGIN")
            self._transaction_cursor=cursor
            self._transaction_level=1
        else:
            # a transaction already exists, use a new savepoint
            level=self._transaction_level+1
            self._transaction_cursor.execute(f"SAVEPOINT svp{level}") # pyright: ignore
            self._transaction_level=level

    def transaction_commit(self):
        """Commit (write any changes) to a transaction"""
        if self._ro_mode:
            raise Exception("Database is read-only")
        if not self._transaction_cursor:
            raise Exception("No transaction is started, can't commit")
        if self._transaction_level==1:
            # last outer transaction level, perform a real commit
            self._transaction_cursor.execute("COMMIT")
            self._transaction_cursor.close()
            self._transaction_cursor=None
            self._transaction_level=0
        else:
            # still nested, "commit" the inner savepoint
            self._transaction_cursor.execute(f"RELEASE SAVEPOINT svp{self._transaction_level}")
            self._transaction_level-=1

    def transaction_rollback(self):
        """Rolls back (cancels any changes) a transaction"""
        if not self._transaction_cursor:
            raise Exception("No transaction is started, can't roll back")

        if self._transaction_level==1:
            # last outer transaction level, perform a real rollback
            try:
                self._transaction_cursor.execute("ROLLBACK")
                self._transaction_cursor.close()
            except Exception as e:
                logging.log(logging.ERROR, f"{str(e)} (while Rolling back transaction)")
            finally:
                self._transaction_cursor=None
                self._transaction_level=0
        else:
            # still nested, rollback the inner savepoint
            self._transaction_cursor.execute(f"ROLLBACK TO SAVEPOINT svp{self._transaction_level}")
            self._transaction_level-=1

    def _get_cursor(self, force=False) -> sqlite3.Cursor:
        # create or use a transaction's cursor. If @force is True, a new cursor is always created
        if self._conn is None:
            raise Exception("Dabase is closed")
        counter=database_locked_timeout
        last_e=None
        while counter>0:
            try:
                if force:
                    return self._conn.cursor() # may fail if the object is being accessed from a thread different than the one it was created in
                elif self._transaction_cursor:
                    return self._transaction_cursor
                else:
                    return self._conn.cursor() # may fail if the object is being accessed from a thread different than the one it was created in
            except sqlite3.OperationalError as e:
                    if str(e)=="database is locked":
                        logging.log(logging.ERROR, f"Database '{self._db_file}' is locked, opened by programs {self._locker_processes_as_string()}")
                        counter-=1
                        last_e=e
                        time.sleep(0) # yield control to something else
                    else:
                        raise e
        raise last_e # pyright: ignore

    def select_with_func(self, sql:str, parameters:dict|None, cb_function, *args):
        """Execute a SELECT command with the @parameters named parameters (or None) and calls @cb_function with @args
        with the sqlite3.Cursor object as 1st argument. The @cb_function is called once and my return something which
        will the be returned by this method.

        NB:
        - avoid long operations in the cb_function() treatment, as it might incur database locks
        - if a transation was started, then _DON'T_ call any other select*() or execute() from within the @cb_function
          as unexpected results will occur (the same SQLite3 cursor object being reused)

        ex:
            def mycb(rows, *args):
                res=[]
                for row in rows:
                    print("%s => %s"%(args, row))
                    res.append(row[0])
                return res

            import fairshell.box.db as db
            db=db.DB()
            res=db.select_with_func("SELECT boxid from boxes", None, mycb, 12)
        """
        cf=sql[0:7].casefold()
        if cf!="select ":
            raise Exception("Not an SQL SELECT command")
        c=None
        last_e=None
        try:
            c=self._get_cursor()
            counter=database_locked_timeout
            if parameters is None:
                parameters={}
            while counter>0:
                try:
                    data=c.execute(sql, parameters)
                    return cb_function(data, *args)
                except sqlite3.OperationalError as e:
                    if str(e)=="database is locked":
                        logging.log(logging.ERROR, f"Database '{self._db_file}' is locked, opened by programs {self._locker_processes_as_string()}")
                        counter-=1
                        last_e=e
                        time.sleep(0) # yield control to something else
                    else:
                        raise e
        finally:
            if not self._transaction_cursor and c is not None:
                c.close()
        raise last_e # pyright: ignore

    def select_1st_row(self, sql, parameters:dict|None=None, check_max_one_row:bool=True) -> list|None:
        """Execute a SELECT command and returns the 1st row of the result, or None if there is no result,
        The result is a tuple, with a value for each selected column

        NB: an exception is raised if there is more than one row
        """
        def _get_first(rows, sql):
            if check_max_one_row:
                therow=None
                for row in rows:
                    if therow:
                        raise Exception("More than one DB row returned when at most one was expected")
                    therow=row
                return therow
            else:
                for row in rows:
                    return row
                return None

        data=self.select_with_func(sql, parameters, _get_first, sql)
        return list(data) if data is not None else None

    def select_all(self, sql, parameters:dict|None=None) -> list[list]:
        """Execute a SELECT command and returns the 1st row of the result, or None if there is no result,
        The result is a tuple of tuples with a value for each selected column.
        The empty tuple is returned if the SELECT does not return any data.
        """
        def _get_all(rows):
            return [list(row) for row in rows]
        return self.select_with_func(sql, parameters, _get_all)

    def execute(self, sql:str, parameters:dict|None=None):
        """Execute any non SELECT kind of command, for SELECT commands, use select_*() instead."""
        if self._ro_mode and sql[:7].casefold()!="pragma ":
            raise Exception("Database is read-only")
        c=None
        try:
            last_e=None
            c=self._get_cursor()
            counter=database_locked_timeout
            if parameters is None:
                parameters={}
            while counter>0:
                try:
                    c.execute(sql, parameters)
                    return
                except sqlite3.OperationalError as e:
                    if str(e)=="database is locked":
                        logging.log(logging.ERROR, f"Database '{self._db_file}' is locked, opened by programs {self._locker_processes_as_string()}")
                        counter-=1
                        last_e=e
                        time.sleep(0) # yield control to something else
                    else:
                        raise e
        finally:
            if not self._transaction_cursor and c is not None:
                c.close()
        raise last_e # pyright: ignore
