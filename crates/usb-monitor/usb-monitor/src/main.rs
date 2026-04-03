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

use std::env;
use std::io::{stdout, Write};
use aya::{maps::RingBuf, programs::TracePoint};
#[rustfmt::skip]
use tokio::signal;
use tracing_log::LogTracer;
use padsi::trace::{TraceConfig, tracing_setup_json, info, debug, warn, error};
use usb_monitor_common::Event;

// Monitors open() and close() of files under /dev/bus/usb/* and print notifications on stdout
// The actual monitored syscalls are 'open', 'openat', 'openat2', and 'close'

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
        "/usb-monitor"
    )))?;

    // set up logging
    println!("Setting up logging");
    let mut log_dir=String::from("/var/log");
    if let Ok(v)=env::var("LOG_DIR") {
        log_dir=String::from(v)
    }
    println!("Logging to directory '{}'", log_dir);
    let trace_conf= TraceConfig::new(&log_dir, "usb-monitor")
        .with_stdout_output(false);
    let _t=tracing_setup_json(&trace_conf).expect("Failed to initialize logging");
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
    let program: &mut TracePoint = ebpf.program_mut("enter_openat").unwrap().try_into()?;
    program.load()?;
    program.attach("syscalls", "sys_enter_openat")?;

    let program: &mut TracePoint = ebpf.program_mut("enter_open").unwrap().try_into()?;
    program.load()?;
    program.attach("syscalls", "sys_enter_open")?;

    let program: &mut TracePoint = ebpf.program_mut("enter_openat2").unwrap().try_into()?;
    program.load()?;
    program.attach("syscalls", "sys_enter_openat2")?;

    let program: &mut TracePoint = ebpf.program_mut("exit_openat").unwrap().try_into()?;
    program.load()?;
    program.attach("syscalls", "sys_exit_openat")?;

    let program: &mut TracePoint = ebpf.program_mut("exit_open").unwrap().try_into()?;
    program.load()?;
    program.attach("syscalls", "sys_exit_open")?;

    let program: &mut TracePoint = ebpf.program_mut("exit_openat2").unwrap().try_into()?;
    program.load()?;
    program.attach("syscalls", "sys_exit_openat2")?;

    let program: &mut TracePoint = ebpf.program_mut("enter_close").unwrap().try_into()?;
    program.load()?;
    program.attach("syscalls", "sys_enter_close")?;

    let program: &mut TracePoint = ebpf.program_mut("sched_process_exit").unwrap().try_into()?;
    program.load()?;
    program.attach("sched", "sched_process_exit")?;

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

    let comm=match str::from_utf8(&event.comm) {
        Ok(c) => c.trim_end_matches('\0'),
        Err(_err) => "N/A"
    };
    match str::from_utf8(&event.file) {
        Ok(fname) => {
            let fname=fname.trim_end_matches('\0');
            info!(fd=event.fd, uid=event.uid, pid=event.pid, file=fname, %comm,
                "{}", event.ev_type.to_string());

            let data=format!("{};{};{};{}", event.ev_type, event.uid, event.pid, fname);
            println!("{}", data);
            if let Err(err)=stdout().flush() {
                error!("Could not flush stdout: {}", err.to_string())
            }
        },
        Err(_) => {
            error!("invalid UTF-8 file name in event")
        }
    }
    Ok(())
}
