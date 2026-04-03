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
# Database to store the events associated to a VM version
#

import datetime
import enum
from dataclasses import dataclass

from . import db

_db_schema={
    1: [
        "CREATE TABLE events (ts INTEGER NOT NULL, type INTEGER NOT NULL, descr STRING)", # to record all events on the VM
        "CREATE INDEX eventsidx ON events (ts, type)",
        "CREATE TABLE attributes (name STRING NOT NULL PRIMARY KEY, value STRING)", # VM's attributes like its domain name
        "CREATE INDEX attributesidx ON attributes (name)"
    ],
}

class EventType(int, enum.Enum):
    VM_CREATED = 0
    VM_STARTED = 1
    VM_SHUTDOWN = 2
    VM_DISCARDED = 3
    ATTRIBUTE_SET = 4
    ATTRIBUTE_UNSET = 5
    INFORMATIONAL = 6

def _evtype_to_str(evtype:EventType) -> str:
    match evtype:
        case EventType.VM_CREATED:
            return "VM creation"
        case EventType.VM_STARTED:
            return "VM started"
        case EventType.VM_SHUTDOWN:
            return "VM shut down"
        case EventType.VM_DISCARDED:
            return "VM discarded"
        case EventType.ATTRIBUTE_SET:
            return "Attribute set"
        case EventType.ATTRIBUTE_UNSET:
            return "Attribute unset"
        case EventType.INFORMATIONAL:
            return "Information"
        case _:
            raise Exception(f"Unhandled EventType '{evtype}'")

@dataclass
class Event:
    ts: int
    type: EventType
    descr: str

    def __str__(self) -> str:
        dt=datetime.datetime.fromtimestamp(self.ts)
        return f"@{str(dt)} [{_evtype_to_str(self.type)}] {self.descr}"

    def __eq__(self, other):
        return self.ts==other.ts and self.type==other.type and self.descr==other.descr

class VMDB:
    def __init__(self, dbfile:str):
        self._db=db.Sqlite3DB(dbfile, _db_schema, use_wal=False)
        data=self._db.select_1st_row("SELECT count(ts) FROM events")
        if data[0]==0:
            self.add_event(EventType.VM_CREATED, "Initial creation")

    # context manager
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._db.close()

    def __del__(self):
        self._db.close()

    def is_being_used(self) -> bool:
        """Returns True if at least one process is using the database
        """
        return self._db.is_being_used()

    @property
    def state(self) -> str:
        """Get the VM's state as a string
        """
        events=self.get_events([EventType.VM_SHUTDOWN, EventType.VM_DISCARDED, EventType.VM_STARTED, EventType.VM_CREATED], count_limit=1)
        if len(events)==0:
            raise Exception("No event recorded for the VM version")

        ev=events[0]
        from . import version
        match ev.type:
            case EventType.VM_CREATED:
                return version.VMState.CREATED.value
            case EventType.VM_STARTED:
                return version.VMState.RUNNING.value
            case EventType.VM_SHUTDOWN:
                return version.VMState.STOPPED.value
            case EventType.VM_DISCARDED:
                return version.VMState.DISCARDED.value
        raise Exception(f"CODEBUG, got event of type '{ev.type}'")

    def set_state(self, state:str, context:str|None=None) -> int:
        """Define the state
        Returns the timestamp of the recorded event
        """
        from . import version
        match state:
            case version.VMState.CREATED.value:
                evtype=EventType.VM_CREATED
            case version.VMState.RUNNING.value:
                evtype=EventType.VM_STARTED
            case version.VMState.STOPPED.value:
                evtype=EventType.VM_SHUTDOWN
            case version.VMState.DISCARDED.value:
                evtype=EventType.VM_DISCARDED
            case _:
                raise Exception(f"CODEBUG, unhandled VM state '{state}'")
        return self.add_event(evtype, f"Set to state to {state}" if not context else context)

    @property
    def nickname(self) -> str|None:
        return self._get_attribute("nickname")

    @nickname.setter
    def nickname(self, nickname:str|None):
        self._set_attribute("nickname", nickname)

    def _set_attribute(self, name:str, value:str|int|None):
        if not isinstance(name, str) or not name:
            raise Exception(f"Invalid attribute name '{name}'")
        if not value:
            try:
                self._db.transaction_begin()
                self._db.execute("DELETE FROM attributes where name=:name", {"name": "name"})
                self.add_event(EventType.ATTRIBUTE_UNSET, f"Unset attribute '{name}'")
                self._db.transaction_commit()
            except Exception as e:
                self._db.transaction_rollback()
                raise e
        else:
            try:
                self._db.transaction_begin()
                self._db.execute("INSERT OR REPLACE INTO attributes (name, value) VALUES (:name, :value)", {
                    "name": name,
                    "value": value
                })
                self.add_event(EventType.ATTRIBUTE_SET, f"Set attribute '{name}' to '{value}'")
                self._db.transaction_commit()
            except Exception as e:
                self._db.transaction_rollback()
                raise e

    def _get_attribute(self, name:str):
        data=self._db.select_1st_row("SELECT value FROM attributes where name=:name", {"name": name})
        if data is None:
            return None
        return data[0]

    def add_event(self, evtype:EventType, descr:str|None=None, forced_ts:int|None=None) -> int:
        """Record a new event in the DB
        Returns the timestamp of the recorded event
        """
        if isinstance (descr, str):
            descr=descr.strip()
            if not descr:
                descr=None
        now=datetime.datetime.now(datetime.timezone.utc)
        now_ts=int(datetime.datetime.timestamp(now))
        if forced_ts is not None:
            if not isinstance(forced_ts, int) or abs(forced_ts-now_ts)>2:
                raise Exception(f"Invalid forced timestamp '{forced_ts}'")
            now=forced_ts

        self._db.execute("INSERT INTO events (ts, type, descr) VALUES (:now, :type, :descr)", {
            "now": now_ts,
            "type": evtype.value,
            "descr": descr
        })
        return now_ts

    def get_events(self, evtypes:list[EventType]|None=None, count_limit:int|None=None) -> list[Event]:
        """Get the VM's events of the specified type, or all events if not specified,
        sorted from most recent to oldest
        """
        if evtypes is None:
            sql="SELECT ts, type, descr FROM events ORDER BY ts DESC"
        else:
            var=", ".join([str(t.value) for t in evtypes])
            sql=f"SELECT ts, type, descr FROM events WHERE type in ({var}) ORDER BY ts DESC"

        if count_limit is not None:
            if not isinstance(count_limit, int) or count_limit<=0:
                raise Exception(f"Invalid count limit '{count_limit}'")
            sql=f"{sql} LIMIT {count_limit}"

        evdata=self._db.select_all(sql)
        if evdata:
            return [Event(ts=row[0], type=EventType(row[1]), descr=row[2]) for row in evdata]
        return []

    @property
    def last_used(self) -> Event:
        """Get the timestamp (UTC) when the VM was last used
        as the timestamp of its last state event
        """
        events=self.get_events([EventType.VM_SHUTDOWN, EventType.VM_STARTED], count_limit=1)
        if len(events)==1:
            return events[0]
        raise Exception("No VM event recorded for the VM version")
