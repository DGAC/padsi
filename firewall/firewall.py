#
# Copyright (c) 2025-2026 DGAC/DSNA
# Copyright (c) 2024 Vivien Malerba <vmalerba@gmail.com>
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

import fcntl
import ipaddress
import os
import subprocess
import time

from . import nft
from .common import Family, FlowType, LogSpec, Policy
from .netflow import NetFlow


def netns_exists(name:str) -> bool:
    """Tells if a network namespace exists"""
    try:
        p=subprocess.run(["ip", "netns", "list"], capture_output=True, text=True)
    except Exception as e:
        raise Exception(f"Could not list network namespaces: {e}")
    for line in p.stdout.splitlines():
        if line==name or line.startswith(name+" "):
            return True
    return False

class ProcessExclusiveLock:
    """Use this object to ensure that a single process can access a directory or file at any given time"""
    def __init__(self, where):
        """The @where argument can be a directory or a file"""
        self._lock_fp=None
        if  os.path.isdir(where):
            self._lock_file=where+"/.lock"
        elif os.path.isfile(where):
            self._lock_file=where
        else:
            raise Exception(f"Inexistant or unreachable path '{where}'")

    def lock(self):
        counter=0
        while True:
            try:
                if self._lock_fp is not None:
                    raise Exception (f"File '{self._lock_file}' is already locked")
                try:
                    fp=open(self._lock_file, "w")
                    self._lock_fp=fp
                    fcntl.lockf(self._lock_fp, fcntl.LOCK_EX)
                    return
                except Exception as e:
                    self._lock_fp=None
                    raise Exception (f"Could not lock '{self._lock_file}': {str(e)}")
            except Exception as e:
                if counter==100:
                    raise e
                counter+=1
                time.sleep(0.05)

    def unlock(self):
        if self._lock_fp is None:
            raise Exception (f"File '{self._lock_file}' is not locked")
        try:
            fcntl.lockf(self._lock_fp, fcntl.LOCK_UN)
            self._lock_fp=None
        except Exception as e:
            raise Exception (f"Could not unlock '{self._lock_file}': {str(e)}")

    def __del__(self):
        if self._lock_fp is not None:
            self.unlock()

class Firewall:
    def __init__(self, netns:str|None=None, log_denied_spec:LogSpec|None=None, objects_prefix:str|None=None):
        """Netfilter firewall object when can the modify the associated rules
        If netns is specified, it must correspond to an existing network namespace
        If log_denied_spec is not None, then any DENY rule will be logged using that spec.
        The objects_prefix argument allows to create objects with a distinguishable name
        """
        if netns is not None and not netns_exists(netns):
            raise Exception(f"Network namespace {netns} does not exist")
        self._netns=netns

        nft_path=None

        # check if the nft tool is installed
        try:
            subprocess.run(["/sbin/nft", "-V"], capture_output=True)
            nft_path="/sbin/nft"
        except Exception:
            raise Exception("Could not find the 'nft' tool used to configure the netfilter firewall")
        self._fwtool=nft.FwTool(nft_path, self._netns, log_denied_spec, objects_prefix)

        # prepare lock file to prevent multiple processes from modifying netfilter's rules at the same time
        # (of course it only applies to processes which use this object)
        lockdir="/tmp/netfilter"
        os.makedirs(lockdir, exist_ok=True, mode=0o700)
        self._lock=ProcessExclusiveLock(lockdir)

    @property
    def log_denied(self) -> bool:
        """Tells if DENY requests are logged
        """
        return self._fwtool.log_denied

    def set_default_policy(self, flowtype:FlowType, policy:Policy, family:Family=Family.IPv4):
        """Set the default policy for a specified flow type
        """
        self._lock.lock()
        try:
            self._fwtool.set_default_policy(flowtype, policy, family)
        finally:
            self._lock.unlock()

    def get_default_policy(self, flowtype:FlowType, family:Family=Family.IPv4) -> Policy|None:
        """Get the default policy for a specified flow type
        """
        self._lock.lock()
        try:
            return self._fwtool.get_default_policy(flowtype, family)
        finally:
            self._lock.unlock()

    def flush(self, flowtype:FlowType, family:Family=Family.IPv4):
        """Remove all the rules for the specified flow type
        """
        self._lock.lock()
        try:
            return self._fwtool.flush(flowtype, family)
        finally:
            self._lock.unlock()

    def set_related_connections_policy(self, flowtype:FlowType, policy:Policy, family:Family=Family.IPv4):
        self._lock.lock()
        try:
            self._fwtool.set_related_connections_policy(flowtype, policy, family)
        finally:
            self._lock.unlock()

    def get_related_connections_policy(self, flowtype:FlowType, family:Family=Family.IPv4) -> Policy:
        self._lock.lock()
        try:
            return self._fwtool.get_related_connections_policy(flowtype, family)
        finally:
            self._lock.unlock()

    def flow_set_policy(self, flowtype:FlowType, nflow:NetFlow, policy:Policy, log_if_deny:bool=True, family:Family=Family.IPv4):
        """Define a policy rule for a specific flow
        """
        self._lock.lock()
        try:
            self._fwtool.flow_set_policy(flowtype, nflow, policy, log_if_deny, family)
        finally:
            self._lock.unlock()

    def flow_get_policy(self, flowtype:FlowType, nflow:NetFlow) -> Policy|None:
        """Get the current policy rule for a specific flow
        Returns None if no policy has been defined for the specific flow
        """
        self._lock.lock()
        try:
            return self._fwtool.flow_get_policy(flowtype, nflow)
        finally:
            self._lock.unlock()

    def flow_delete_policy(self, flowtype:FlowType, nflow:NetFlow):
        """Delete the policy rule for a specific flow if there is one
        """
        self._lock.lock()
        try:
            self._fwtool.flow_delete_policy(flowtype, nflow)
        finally:
            self._lock.unlock()

    def add_masquerade(self, out_iface:str|None=None, source_addr:ipaddress.IPv4Address|None=None):
        """Add masquerading to postrouting for a specific outbound interface or a specific source address
        Either out_iface or source_addr must be specified.
        """
        self._lock.lock()
        try:
            self._fwtool.add_masquerade(out_iface, source_addr)
        finally:
            self._lock.unlock()

    def del_masquerade(self, out_iface:str|None=None, source_addr:ipaddress.IPv4Address|None=None):
        """Remove masquerading
        """
        self._lock.lock()
        try:
            self._fwtool.del_masquerade(out_iface, source_addr)
        finally:
            self._lock.unlock()

    def del_stale_masquerade(self, out_iface_index:int):
        """Remove a masquerading which relies on an interface which has been removed (hence the "stale" mention)
        """
        self._lock.lock()
        try:
            self._fwtool.del_stale_masquerade(out_iface_index)
        finally:
            self._lock.unlock()

    def add_dnat(self, dest_addr:ipaddress.IPv4Address, in_iface:str|None=None, protocol_spec:str|None=None, port_spec:str|None=None):
        """Add prerouting DNAT to a specified IP address.
        """
        self._lock.lock()
        try:
            self._fwtool.add_dnat(dest_addr, in_iface, protocol_spec, port_spec)
        finally:
            self._lock.unlock()
