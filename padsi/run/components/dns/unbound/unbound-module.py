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
# this script is executed as a plugin by the unbound server
#

import base64
import json
import logging
import os
import platform
import re
import socket
import sys
import syslog
import time
from dataclasses import dataclass

from unboundmodule import (MODULE_ERROR, MODULE_EVENT_MODDONE,
                           MODULE_EVENT_NEW, MODULE_EVENT_PASS,
                           MODULE_FINISHED, MODULE_WAIT_MODULE, PKT_AA, PKT_QR,
                           PKT_RA, RCODE_NOERROR, RCODE_NXDOMAIN, RR_CLASS_IN,
                           RR_TYPE_A, RR_TYPE_ANY, DNSMessage, log_err,
                           log_info, strmodulevent)

_debug=False

resolv_rules_file="/etc/resolv-rules.json"
fw_socket_file="/tmp/dns-fw.sock"

plogger=logging.getLogger(__name__+".mod")
try:
    file_handler=logging.FileHandler("/var/log/resolv.log")
except Exception as e:
    syslog.syslog(syslog.LOG_ERR, f"Could not start logging to /var/log/resolv.log: {str(e)}")
    sys.exit(1)

logging.Formatter.converter=time.gmtime # set the converter to use UTC
formatter=logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%Y%m%d-%H:%M:%S")

file_handler.setFormatter(formatter)
plogger.addHandler(file_handler)
plogger.propagate=False
plogger.setLevel(logging.DEBUG)

socket_client:socket.socket|None=None

@dataclass
class ResolvPatternRule:
    allow:bool
    pattern:re.Pattern

resolv_basic_allow_domains:list[str]=[]
resolv_basic_deny_domains:list[str]=[]
resolv_pattern_rules:list[ResolvPatternRule]=[]

log_only=os.environ.get("LOG_ONLY")=="yes"
if log_only:
    syslog.syslog(syslog.LOG_INFO, "Operating in log only mode (not enforcing block mode)")
else:
    syslog.syslog(syslog.LOG_INFO, "Enforcing block mode")
denied_fallback=os.environ.get("DENIED_FALLBACK_IP")
if denied_fallback is not None:
    syslog.syslog(syslog.LOG_DEBUG, f"Denied fallback IP is {denied_fallback}")

# copy of the function in netflow.py
def domain_to_regex(domain:str) -> str:
    """Create a Regex from a wildcard domain
    """
    if "*" in domain:
        # tmp replace ** with § to avoid confusing the next modifications
        q=domain.replace("**", "§")
        # handle each "label" independantly
        parts=q.split(".")
        nparts=[]
        for p in parts:
            if p=="*":
                nparts.append(r"[^\.\*]+") # at least one character
            else:
                nparts.append(p.replace("*", r"[^\.\*]*")) # zero or more characters

        # don't interpret the dot character as a regex placeholder
        q=r"\.".join(nparts)

        # convert back § to "at least one character", and add start and end markers
        return "^"+q.replace("§", ".*")+"$"
    return domain.replace(".", r"\.")

def init(id, cfg):
    #log_info("pythonmod: init called, module id is %d port: %d script: %s" % (id, cfg.port, cfg.python_script))
    global resolv_basic_allow_domains
    global resolv_basic_deny_domains
    global resolv_pattern_rules
    global socket_client

    if _debug:
        log_info(f"Python (version {platform.python_version()}) module init")

    # load all the resolv. rules
    for entry in json.loads(open(resolv_rules_file, "r").read()):
        query=None
        try:
            query=entry["query"]
            if "*" in query:
                expr=re.compile(domain_to_regex(query))
                resolv_pattern_rules.append(ResolvPatternRule(entry["action"]=="allow", expr))
            elif entry["action"]=="allow":
                resolv_basic_allow_domains.append(query)
            else:
                resolv_basic_deny_domains.append(query)
        except Exception:
            syslog.syslog(syslog.LOG_WARNING, f"Invalid rule's query '{query}'")

    # open the socket to talk to the FW management component (fw_socket_file)
    socket_client=socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    socket_client.connect(fw_socket_file)

    return True

def deinit(id):
    global socket_client
    if socket_client is not None:
        socket_client.close()
        socket_client=None
    return True

def inform_super(id, qstate, superqstate, qdata):
    return True

def get_requester_ip(qstate):
    # determine source IP address
    #   -> see https://adamo.wordpress.com/2018/07/06/unbound-python-and-conditional-replies-based-on-source-ip-address/
    try:
        rl=qstate.mesh_info.reply_list
        q=None
        while rl:
            if rl.query_reply:
                q=rl.query_reply
                break
            rl=rl.next
        if q:
            return q.addr
        return None
    except NameError as e:
        log_err(f"[ERR: {str(e)}]")
        raise e

def get_A_record(data):
    (rdlength, rdata) = (data[:2], data[2:])
    try:
        assert rdlength==b'\x00\x04'
        assert len(rdata)==4
        addr_str=[str(c) for c in rdata]
        return ".".join(addr_str)
    except Exception:
        txt=f"Unhandled A record data {base64.b64encode(data).decode()}"
        log_err(txt)
        plogger.warning(json.dumps({
            "action": "error",
            "text": txt
        }))
        return None

def get_AAAA_record(data):
    try:
        (_rdlength, rdata) = (data[:2], data[2:])
        #assert rdlength==b'\x00\x10'
        #assert len(rdata)==16
        addr_bytes = [c for c in rdata]
        addr_str=[]
        for index in range(0,8):
            if addr_bytes[index]==0:
                sdata="%x"%addr_bytes[index+1]
            else:
                sdata="%x%02x"%(addr_bytes[index],addr_bytes[index+1])
            addr_str+=[sdata]
        return ":".join(addr_str)
    except Exception:
        txt=f"Unhandled AAAA record data: {base64.b64encode(data).decode()}"
        log_err(txt)
        plogger.warning(json.dumps({
            "action": "error",
            "text": txt
        }))
        return None

def get_CNAME_record(data):
    try:
        (_rdlength, rdata) = (data[:2], data[2:])
        i=0
        parts=[]
        while(i < len(rdata)):
            partlen=int(rdata[i])
            part=rdata[i+1:i+1+partlen].decode('utf-8')
            if part:
                parts.append(part)
            i+=partlen+1
        return '.'.join(parts)
    except Exception:
        txt=f"Unhandled CNAME record data: {base64.b64encode(data).decode()}"
        log_err(txt)
        plogger.warning(json.dumps({
            "action": "error",
            "text": txt
        }))
        return None

def operate(id, event, qstate, qdata):
    global resolv_basic_allow_domains
    global resolv_basic_deny_domains
    global resolv_pattern_rules
    #log_info("pythonmod: operate called, id: %d, event:%s" % (id, strmodulevent(event)))

    if event in (MODULE_EVENT_NEW, MODULE_EVENT_PASS): # query was passed from the previous module or new query
        if qstate.qinfo.qclass==RR_CLASS_IN:
            try:
                # determine source IP address
                req_addr=get_requester_ip(qstate)

                # check if query is allowed
                allowed=False
                qname=qstate.qinfo.qname_str # ATTN: FQDN if it ends with a "."
                if qname in resolv_basic_allow_domains:
                    allowed=True
                elif qname in resolv_basic_deny_domains:
                    pass
                else:
                    for rule in resolv_pattern_rules:
                        if re.match(rule.pattern, qname):
                            allowed=rule.allow
                            break

                plogger.info(json.dumps({
                    "action": "req-allow" if allowed else ("req-would-block" if log_only else "req-block"),
                    "name": qname,
                    "type": qstate.qinfo.qtype_str,
                    "from": req_addr
                }))
                if not allowed and not log_only:
                    if denied_fallback is None:
                        # deny request
                        qstate.ext_state[id]=MODULE_FINISHED
                        if _debug:
                            syslog.syslog(syslog.LOG_DEBUG, f"requested {qname}, type {qstate.qinfo.qtype_str} from {req_addr}: blocked")
                        qstate.return_rcode = RCODE_NXDOMAIN
                        qstate.ext_state[id] = MODULE_FINISHED
                        return True
                    else:
                        # redirect to the denied_fallback IP
                        msg=DNSMessage(qstate.qinfo.qname_str, RR_TYPE_A, RR_CLASS_IN, PKT_QR | PKT_RA | PKT_AA)
                        if (qstate.qinfo.qtype == RR_TYPE_A) or (qstate.qinfo.qtype == RR_TYPE_ANY):
                            msg.answer.append(f"{qstate.qinfo.qname_str} 10 IN A {denied_fallback}")
                        if not msg.set_return_msg(qstate):
                            qstate.ext_state[id]=MODULE_ERROR
                            return True

                        qstate.return_msg.rep.security = 2 # we don't need validation, result is valid
                        qstate.return_rcode=RCODE_NOERROR
                        qstate.ext_state[id]=MODULE_FINISHED

                        if _debug:
                            syslog.syslog(syslog.LOG_DEBUG, f"requested {qname}, type {qstate.qinfo.qtype_str} from {req_addr}: fallback to {denied_fallback}")
                        return True

                # Pass on the new event to the iterator
                if _debug:
                    syslog.syslog(syslog.LOG_DEBUG, f"requested {qname}, type {qstate.qinfo.qtype_str} from {req_addr}: allowed")
                qstate.ext_state[id]=MODULE_WAIT_MODULE
                if _debug:
                    syslog.syslog(syslog.LOG_DEBUG, f"requested {qname}, type {qstate.qinfo.qtype_str} from {req_addr}: allowed")
                return True
            except Exception as e:
                log_err(f"ERROR while handling event {strmodulevent(event)}: {str(e)}")
                qstate.ext_state[id]=MODULE_ERROR
                return True
        else:
            log_info(f"Unhandled qstate class '{qstate.qinfo.qclass}'")
            qstate.ext_state[id]=MODULE_WAIT_MODULE
            return True

    elif event==MODULE_EVENT_MODDONE: # we have a response
        # determine source IP address
        req_addr=get_requester_ip(qstate)

        if _debug:
            log_info(f"RESPONSE {qstate.qinfo.qname_str} for query from {req_addr}")
        if qstate.return_msg:
            try:
                # build list of resolved IPs
                rep=qstate.return_msg.rep
                resolved_ips=[]
                for i in range(0, rep.rrset_count):
                    rr=rep.rrsets[i]
                    rk=rr.rk

                    if rk.rrset_class_str=="IN":
                        if rk.type_str=="A":
                            d=rr.entry.data
                            for j in range(0,d.count+d.rrsig_count):
                                ttl=d.rr_ttl[j]
                                rec=get_A_record(d.rr_data[j])
                                if rec:
                                    if _debug:
                                        log_info(f"RESOLVED {qstate.qinfo.qname_str} A => {rec}")
                                    resolved_ips+=[{"TTL": ttl, "A": rec, "AAAA": None}]
                                # TODO: report on the d.security and d.trust values
                        elif rk.type_str=="AAAA":
                            d=rr.entry.data
                            for j in range(0, d.count+d.rrsig_count):
                                ttl=d.rr_ttl[j]
                                rec=get_AAAA_record(d.rr_data[j])
                                if rec:
                                    if _debug:
                                        log_info(f"RESOLVED {qstate.qinfo.qname_str} AAAA => {rec}")
                                    resolved_ips+=[{"TTL": ttl, "A": None, "AAAA": rec}]
                        elif rk.type_str=="CNAME":
                            d=rr.entry.data
                            cnames=[]
                            for j in range(0, d.count+d.rrsig_count):
                                cname=get_CNAME_record(d.rr_data[j])
                                if cname:
                                    # add this CNAME in the allow list
                                    if _debug:
                                        log_info(f"RESOLVED {qstate.qinfo.qname_str} CNAME '{cname}', adding to list of allowed domains")
                                    cnames.append(cname)
                                    cname+="."
                                    if cname not in resolv_basic_allow_domains:
                                        resolv_basic_allow_domains.append(cname)

                            plogger.info(json.dumps({
                                "action": "result",
                                "req": qstate.qinfo.qname_str,
                                "CNAME": cnames,
                                "from": req_addr
                            }))
                        else:
                            # logged only for now
                            d=rr.entry.data
                            try:
                                data=[d.rr_data[j] for j in range(0, d.count+d.rrsig_count)]
                                plogger.info(json.dumps({
                                    "action": "result",
                                    "req": qstate.qinfo.qname_str,
                                    rk.type_str: data,
                                    "from": req_addr
                                }))
                            except Exception:
                                data=[base64.b64encode(d.rr_data[j]).decode() for j in range(0, d.count+d.rrsig_count)]
                                plogger.info(json.dumps({
                                    "action": "result",
                                    "req": qstate.qinfo.qname_str,
                                    rk.type_str: data,
                                    "from": req_addr
                                }))

                if len(resolved_ips)>0:
                    # log the resolution to a TMP file so the host can monitor it and modify the FW rules
                    # accordingly
                    plogger.info(json.dumps({
                        "action": "result",
                        "req": qstate.qinfo.qname_str,
                        "A-AAAA": resolved_ips,
                        "from": req_addr
                    }))

                    # notify the FW via its socket
                    data={
                        "query": qstate.qinfo.qname_str,
                        "resolv": resolved_ips
                    }
                    if socket_client is None:
                        syslog.syslog(syslog.LOG_ERR, "CODEBUG: socket_client is None")
                    else:
                        socket_client.sendall(json.dumps(data).encode())
                        resp=socket_client.recv(1024)
                        if resp!=b"Ok":
                            syslog.syslog(syslog.LOG_ERR, f"Failed ro send resolv. IPs to FW server: {resp}")
            except Exception as e:
                log_err(f"ERROR while handling response: {str(e)}")

        qstate.ext_state[id]=MODULE_FINISHED
        return True
    else:
        log_err("Unhandled event %s"%event)
        qstate.ext_state[id]=MODULE_ERROR
        return True
