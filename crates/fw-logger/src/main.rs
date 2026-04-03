//
// Copyright (c) 2025-2026 DGAC/DSNA
//
// This file is part of PADSI.
//
// This software is free software: you can redistribute it and/or modify
// it under the terms of the GNU General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.
//
// This software is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
// GNU General Public License for more details.
//
// You should have received a copy of the GNU General Public License
// along with this software.  If not, see <http://www.gnu.org/licenses/>.
//

use libc;
use std::{collections::HashMap, env};
use nflog::{Message, Queue};
use anyhow::{anyhow, Result};
use base64::{Engine, engine::general_purpose::STANDARD};
use netdev::interface::get_interfaces;
use padsi::trace::{LevelFilter, TraceConfig, error, info, tracing_setup_json, warn};

struct NetState {
    ifaces: HashMap<u32, String>
}

///
/// Keep track of the interfaces names from their associated index
///
impl NetState {
    fn new() -> Self {
        Self {ifaces: HashMap::new()}
    }

    fn update(&mut self) -> Result<()> {
        let ifaces=get_interfaces();
        let mut nhash=HashMap::<u32, String>::new();
        for iface in ifaces {
            nhash.insert(iface.index, iface.name.clone());
        }
        self.ifaces=nhash;
        Ok(())
    }

    fn get_from_index(&mut self, index:u32) -> Result<String> {
        match self.ifaces.get(&index) {
            Some(name) => Ok(name.clone()),
            None => {
                match self.update() {
                    Ok(_) => {
                        match self.ifaces.get(&index) {
                            Some(name) => Ok(name.clone()),
                            None => Err(anyhow!("no interface with index {}", index))
                        }
                    },
                    Err(err) => Err(anyhow!(err.to_string()))
                }
            }
        }
    }
}

use std::sync::Mutex;
static CURRENT_STATE: Mutex<Option<NetState>>=Mutex::new(None);

fn main() -> Result<()> {
    // init logging
    let mut log_dir=String::from("/var/log");
    if let Ok(v)=env::var("LOG_DIR") {
        log_dir=String::from(v)
    }
    println!("Logging to directory '{}'", log_dir);
    let trace_conf= TraceConfig::new(&log_dir, "firewall")
        .with_stdout_output(false)
        .with_file_level(LevelFilter::INFO)
        .with_syslog_level(LevelFilter::WARN);
    let _t=tracing_setup_json(&trace_conf).expect("Failed to initialize logging");

    // configuration
    let mut nflog_group=2;
    if let Ok(v)=env::var("LOG_GROUP") {
        nflog_group=v.parse().expect(&format!("Invalid LOG_GROUP value '{}'", v));
    }
    println!("Starting NFLog capture on group {}...", nflog_group);

    // Open NFLOG queue
    let queue=Queue::open()?;
    let _=queue.unbind(libc::AF_INET);
    queue.bind(libc::AF_INET)?;

    let mut group=match queue.bind_group(nflog_group) {
        Ok(g) => g,
        Err(err) => {
            let msg=format!("Failed to bind to group {}: {} (may be used by another program?)", nflog_group, err.to_string());
            error!(msg);
            return Err(anyhow!(msg))
        }
    };
    group.set_mode(nflog::CopyMode::Packet, 0xffff);
    group.set_flags(nflog::Flags::SEQUENCE);
    println!("Waiting for packets... (Ctrl+C to stop)");

    // init network's state
    let mut guard=CURRENT_STATE.lock().unwrap();
    guard.replace(NetState::new());
    drop(guard);

    // Process messages
    group.set_callback(Box::new(msg_received));
    queue.run_loop();
}

fn msg_received(msg: Message) {
    let mut ip_src:Option<String>=None;
    let mut ip_dst:Option<String>=None;
    let mut protocol:Option<String>=None;
    let mut details=PacketDetails::default();
    let mut error:Option<String>=None;
    let payload=msg.get_payload();
    if payload.len() >= 20 {
        // Parse basic IPv4 header
        let version=(payload[0] >> 4) & 0x0F;
        if version == 4 {
            ip_src=Some(format!("{}.{}.{}.{}", payload[12], payload[13], payload[14], payload[15]));
            ip_dst=Some(format!("{}.{}.{}.{}", payload[16], payload[17], payload[18], payload[19]));
            protocol=match payload[9] {
                1 => Some(String::from("ICMP")),
                6 => Some(String::from("TCP")),
                17 => Some(String::from("UDP")),
                p => Some(format!("{}", p))
            };
            let header_len=(payload[0] & 0x0F) as usize * 4;
            match extract_details(payload, header_len, payload[9]) {
                Ok(d) => {details=d},
                Err(err) => {error=Some(err.to_string())}
            }
        }
    }

    let prefix=String::from(msg.get_prefix().to_string_lossy());
    let mut guard=CURRENT_STATE.lock().unwrap();
    let nstate=guard.as_mut().unwrap();

    let iiface:Option<String>=match msg.get_indev() {
        0=>None,
        index => match nstate.get_from_index(index) {
            Ok(name) => Some(name),
            Err(err) => {
                warn!("{}", err.to_string());
                None
            }
        }
    };
    let oiface:Option<String>=match msg.get_outdev() {
        0=>None,
        index => match nstate.get_from_index(index) {
            Ok(name) => Some(name),
            Err(err) => {
                warn!("{}", err.to_string());
                None
            }
        }
    };

    // base64 encode payload for later analysis
    let bpayload=match error {
        Some(_) => Some(STANDARD.encode(payload)),
        None => None
    };

    // log the packet's data
    info!(%prefix, in_iface=iiface, out_iface=oiface,
        ip_src=ip_src, ip_dst=ip_dst,
        port_src=details.port_src, port_dst=details.port_dst,
        flags=details.flags, icmp=details.icmp, dns_infos=details.dns_infos,
        proto=protocol, error=error, payload=bpayload);
}

#[derive(Default)]
struct PacketDetails {
    port_src: Option<u16>,
    port_dst: Option<u16>,
    flags: Option<String>,
    icmp: Option<String>,
    dns_infos: Option<String>
}

fn extract_details(payload: &[u8], ip_header_len: usize, protocol: u8) -> Result<PacketDetails> {
    // Check if we have enough data for the transport layer header
    if payload.len() < ip_header_len + 4 {
        return Err(anyhow!("Invalid packet payload"));
    }
    let transport_start=ip_header_len;
    match protocol {
        6 => {
            // TCP: ports are at offset 0 and 2
            if payload.len() >= transport_start + 4 {
                let src_port=u16::from_be_bytes([
                    payload[transport_start],
                    payload[transport_start + 1]
                ]);
                let dst_port=u16::from_be_bytes([
                    payload[transport_start + 2],
                    payload[transport_start + 3]
                ]);

                // Extract TCP flags if available
                let mut flags_info=String::new();
                if payload.len() >= transport_start + 14 {
                    let flags=payload[transport_start + 13];
                    let mut flag_strs=Vec::new();
                    if flags & 0x01 != 0 { flag_strs.push("FIN"); }
                    if flags & 0x02 != 0 { flag_strs.push("SYN"); }
                    if flags & 0x04 != 0 { flag_strs.push("RST"); }
                    if flags & 0x08 != 0 { flag_strs.push("PSH"); }
                    if flags & 0x10 != 0 { flag_strs.push("ACK"); }
                    if flags & 0x20 != 0 { flag_strs.push("URG"); }

                    if !flag_strs.is_empty() {
                        flags_info=format!("{}", flag_strs.join(","));
                    }
                }

                Ok(PacketDetails{
                    port_src: Some(src_port),
                    port_dst: Some(dst_port),
                    flags: Some(flags_info),
                    icmp: None,
                    dns_infos: None
                })
            } else {
                Err(anyhow!("Invalid packet payload"))
            }
        }
        17 => {
            // UDP: ports are at offset 0 and 2
            if payload.len() >= transport_start + 4 {
                let src_port=u16::from_be_bytes([
                    payload[transport_start],
                    payload[transport_start + 1]
                ]);
                let dst_port=u16::from_be_bytes([
                    payload[transport_start + 2],
                    payload[transport_start + 3]
                ]);
                let dns_infos=parse_dns(payload, transport_start);
                Ok(PacketDetails{
                    port_src: Some(src_port),
                    port_dst: Some(dst_port),
                    flags: None,
                    icmp: None,
                    dns_infos
                })
            } else {
                Err(anyhow!("Invalid packet payload"))
            }
        }
        1 => {
            // ICMP: extract type and code
            if payload.len() >= transport_start + 2 {
                let icmp_type=payload[transport_start];
                let icmp_code=payload[transport_start + 1];
                Ok(PacketDetails{
                    port_src: None,
                    port_dst: None,
                    flags: None,
                    icmp: Some(format!("type={} code={}", icmp_type, icmp_code)),
                    dns_infos: None
                })
            } else {
                Err(anyhow!("Invalid packet payload"))
            }
        }
        _ => Ok(PacketDetails::default())
    }
}

fn parse_dns(payload: &[u8], transport_start: usize) -> Option<String> {
    // UDP header is 8 bytes, DNS starts after that
    let dns_start=transport_start + 8;

    // DNS header is at least 12 bytes
    if payload.len() < dns_start + 12 {
        return None;
    }

    // Parse DNS header
    let transaction_id=u16::from_be_bytes([
        payload[dns_start],
        payload[dns_start + 1]
    ]);

    let flags=u16::from_be_bytes([
        payload[dns_start + 2],
        payload[dns_start + 3]
    ]);

    let qr=(flags >> 15) & 0x01; // 0=query, 1=response

    // parse question section first (needed for both query and response)
    let mut pos=dns_start + 12;
    let mut query_name=String::new();
    let qdcount=u16::from_be_bytes([
        payload[dns_start + 4],
        payload[dns_start + 5]
    ]);
    if qdcount>0 {
        if let Some((domain, new_pos))=parse_dns_name(payload, pos) {
            query_name=domain;
            pos=new_pos;

            // skip QTYPE and QCLASS (4 bytes)
            if payload.len() >= pos + 4 {
                if qr == 0 {
                    let qtype=u16::from_be_bytes([payload[pos], payload[pos + 1]]);
                    return Some(format!("query_for={}, type={}({}), ID={}", query_name, qtype, dns_query_type_to_str(qtype), transaction_id));
                }
            }
        }
    }

    // parse answer section for responses
    if qr==1 {
        let rcode=flags & 0x0F; // response code
        return Some(format!("resp_for={}, code={}({}), ID={}", query_name, rcode, reply_type_to_str(rcode), transaction_id));
    }
    None
}

fn dns_query_type_to_str(qtype: u16) -> &'static str {
    match qtype {
        1 => "A",
        2 => "NS",
        5 => "CNAME",
        6 => "SOA",
        12 => "PTR",
        15 => "MX",
        16 => "TXT",
        28 => "AAAA",
        33 => "SRV",
        255 => "ANY",
        _ => "UNKNOWN",
    }
}

fn reply_type_to_str(rcode:u16) -> &'static str {
    match rcode {
        0 => "NOERROR",
        1 => "FORMERR",
        2 => "SERVFAIL",
        3 => "NXDOMAIN",
        4 => "NOTIMP",
        5 => "REFUSED",
        _ => "UNKNOWN",
    }
}

fn parse_dns_name(payload: &[u8], mut pos: usize) -> Option<(String, usize)> {
    let mut domain=String::new();
    let mut jumped=false;
    let mut jump_pos=0;
    let max_jumps=5; // Prevent infinite loops
    let mut jumps=0;

    loop {
        if pos >= payload.len() {
            return None;
        }

        let len=payload[pos];

        // Check for compression pointer (top 2 bits set)
        if len & 0xC0 == 0xC0 {
            if pos + 1 >= payload.len() {
                return None;
            }

            if !jumped {
                jump_pos=pos + 2;
            }

            // Calculate offset
            let offset=(((len & 0x3F) as usize) << 8) | (payload[pos + 1] as usize);
            pos=offset;
            jumped=true;
            jumps += 1;

            if jumps > max_jumps {
                return None; // Prevent infinite loops
            }
            continue;
        }

        if len == 0 {
            // End of domain name
            if domain.is_empty() {
                return None
            }
            if jumped {
                return Some((domain, jump_pos));
            } else {
                return Some((domain, pos + 1));
            }
        }

        if len > 63 {
            return None; // Invalid label length
        }

        pos += 1;

        if pos + (len as usize) > payload.len() {
            return None;
        }

        // Add separator if not first label
        if !domain.is_empty() {
            domain.push('.');
        }

        // Extract label
        for i in 0..(len as usize) {
            let c=payload[pos + i] as char;
            if c.is_ascii_alphanumeric() || c == '-' || c == '_' {
                domain.push(c);
            } else {
                domain.push('?'); // Invalid character
            }
        }

        pos += len as usize;
    }
}
