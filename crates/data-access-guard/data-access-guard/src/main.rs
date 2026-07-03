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

use aya::{
    Btf,
    maps::{HashMap, RingBuf},
    programs::Lsm,
};
#[rustfmt::skip]
use tokio::signal;
use data_access_guard_common::{Event, KEY_INIT_NETNS, KEY_PADSI_PID};
use padsi::trace::{TraceConfig, debug, info, tracing_setup_json, warn};
use procfs::process::Process;
use std::convert::TryFrom;
use std::{env, ffi::OsString};
use tracing_log::LogTracer;

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    // Bump the memlock rlimit. This is needed for older kernels that don't use the
    // new memcg based accounting, see https://lwn.net/Articles/837122/
    let rlim = libc::rlimit {
        rlim_cur: libc::RLIM_INFINITY,
        rlim_max: libc::RLIM_INFINITY,
    };
    let ret = unsafe { libc::setrlimit(libc::RLIMIT_MEMLOCK, &rlim) };
    if ret != 0 {
        debug!("remove limit on locked memory failed, ret is: {ret}");
    }

    // include eBPF object as raw bytes at compile-time and load it (allows to have a single binary file)
    let mut ebpf = aya::Ebpf::load(aya::include_bytes_aligned!(concat!(
        env!("OUT_DIR"),
        "/data-access-guard"
    )))?;

    // set up logging
    println!("Setting up logging");
    let mut log_dir = String::from("/var/log");
    if let Ok(v) = env::var("LOG_DIR") {
        log_dir = String::from(v)
    }
    println!("Logging to directory '{}'", log_dir);
    let trace_conf = TraceConfig::new(&log_dir, "data-access-guard").with_stdout_output(false);
    let _t = tracing_setup_json(&trace_conf).expect("Failed to initialize logging");
    LogTracer::init()?; // Bridge log crate to tracing

    match aya_log::EbpfLogger::init(&mut ebpf) {
        Err(e) => {
            // This can happen if you remove all log statements from your eBPF program.
            warn!("failed to initialize eBPF logger: {e}");
        }
        Ok(logger) => {
            let mut logger =
                tokio::io::unix::AsyncFd::with_interest(logger, tokio::io::Interest::READABLE)?;
            tokio::task::spawn(async move {
                loop {
                    let mut guard = logger.readable_mut().await.unwrap();
                    guard.get_inner_mut().flush();
                    guard.clear_ready();
                }
            });
        }
    }

    println!("Loading eBPF program");
    let btf = Btf::from_sys_fs()?;
    let program: &mut Lsm = ebpf.program_mut("file_open").unwrap().try_into()?;
    program.load("file_open", &btf)?;
    program.attach()?;
    let program: &mut Lsm = ebpf.program_mut("path_unlink").unwrap().try_into()?;
    program.load("path_unlink", &btf)?;
    program.attach()?;

    // get the CONFIG map
    let mut net_ns_map: HashMap<_, u8, u64> =
        HashMap::try_from(ebpf.map_mut("CONFIG").expect("WTF? no CONFIG map!"))?;

    // add PADSI's system service PID to the map
    let self_proc = Process::myself().expect("Failed to get information about current process");
    let ppid = self_proc
        .stat()
        .expect("Failed to get PPID of the current process")
        .ppid;
    let svce_proc = Process::new(ppid).expect("Failed to get information about parent process");

    let ppid: i32 = match env::var("PADSI_PID") {
        Ok(value) => value.parse()?,
        Err(_err) => svce_proc.pid,
    };
    if ppid <= 0 {
        panic!("Code bug: PID is negative!")
    }
    println!("Padsi's system service's PID is {}", ppid);
    net_ns_map.insert(&KEY_PADSI_PID, ppid as u64, 0)?; // PID can never however be negative

    // add PADSI's system service network namespace to the mapp
    let ns_h = svce_proc
        .namespaces()
        .expect("Failed to get the namespaces of the parent process");
    let net_ns = ns_h
        .0
        .get(&OsString::from("net"))
        .expect("Failed to the the net namespace");
    println!("Init net NS is {}", net_ns.identifier);
    net_ns_map.insert(&KEY_INIT_NETNS, &net_ns.identifier, 0)?;

    let mut ring_buf = RingBuf::try_from(ebpf.map_mut("EVENTS").expect("WTF? no EVENTS map!"))?;
    // Process events in a loop
    loop {
        tokio::select! {
            _ = signal::ctrl_c() => {
                println!("\nExiting...");
                break;
            }
            _ = tokio::time::sleep(tokio::time::Duration::from_millis(100)) => {
                // Poll the ring buffer for new events
                while let Some(item) = ring_buf.next() {
                    process_event(&item)?;
                }
            }
        }
    }
    Ok(())
}

fn process_event(data: &[u8]) -> Result<(), anyhow::Error> {
    // Parse the event data
    if data.len() < std::mem::size_of::<Event>() {
        eprintln!("Invalid event size: {}", data.len());
        return Ok(());
    }

    // Safety: We've verified the size and Event implements Pod
    let event = unsafe { &*(data.as_ptr() as *const Event) };

    // Extract process name (null-terminated string)
    let comm = str::from_utf8(&event.comm)
        .unwrap_or("<invalid>")
        .trim_end_matches('\0');
    let fname = match str::from_utf8(&event.file) {
        Ok(s) => Some(s.trim_end_matches('\0')),
        Err(_) => None,
    };
    let ct = format!("{}", event.call_type);
    info!(
        pid = event.pid,
        uid = event.uid,
        call = ct,
        command = comm,
        ts = event.timestamp,
        file = fname,
        "blocked"
    );
    println!(
        "Event: call_type={}, pid={}, uid={}, comm={}, timestamp={}, file={:?}",
        event.call_type, event.pid, event.uid, comm, event.timestamp, fname
    );

    Ok(())
}
