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

#![no_std]
#![no_main]

use aya_ebpf::helpers::{
    bpf_get_current_comm, bpf_get_current_pid_tgid, bpf_get_current_uid_gid,
    bpf_probe_read_user_str_bytes,
};
use aya_ebpf::{
    macros::{map, tracepoint},
    maps::{HashMap, RingBuf},
    programs::TracePointContext,
};
use aya_log_ebpf::{error, warn};

use usb_monitor_common::{BUF_PATH_LEN, Event, EventType};

const USB_PATH_PREFIX: &[u8] = b"/dev/bus/usb/";

// RingBuf map to send events to userspace
#[map]
static EVENTS: RingBuf = RingBuf::with_byte_size(4096, 0);

// to keep track of information between the enter_ and exit_ of each syscall
// key: PID, value=file name
#[map(name = "TMP")]
static TMP: HashMap<u64, [u8; BUF_PATH_LEN]> = HashMap::with_max_entries(16, 0);

// to keep track of all the FD opened for any PID
// key: PID, value: list of opened FD
const PROC_OPENED_FD_LEN: usize = 16;
#[map(name = "PROC")]
static PROC: HashMap<u64, [u64; PROC_OPENED_FD_LEN]> = HashMap::with_max_entries(16, 0);

// to keep track of opened devices ("active")
#[map(name = "ACTIVE")]
static ACTIVE: HashMap<ActiveKey, [u8; BUF_PATH_LEN]> = HashMap::with_max_entries(16, 0);

struct ActiveKey {
    _tgid: u64, // keep u64 as weird errors happpen when u32 is used
    _fd: u64,
}

//
// Enter open()
//

// From /sys/kernel/debug/tracing/events/syscalls/sys_enter_open/format
#[repr(C)]
struct SysEnterOpen {
    // Common inaccessible fields
    common_type: u16,
    common_flags: u8,
    common_preempt_count: u8,
    common_pid: i32,

    // syscall-specific fields
    __syscall_nr: i32,
    filename: u64, // pointer to filename
    flags: u64,
    mode: u64,
}

#[tracepoint]
pub fn enter_open(ctx: TracePointContext) -> u32 {
    match handle_enter_open(ctx) {
        Ok(ret) => ret,
        Err(ret) => ret,
    }
}

fn handle_enter_open(ctx: TracePointContext) -> Result<u32, u32> {
    let args: SysEnterOpen = unsafe {
        match ctx.read_at(0) {
            Ok(r) => r,
            Err(err) => {
                error!(&ctx, "Failed to get args in enter_open(): error #{}", err);
                return Ok(0);
            }
        }
    };
    let mut filename_buf = [0u8; BUF_PATH_LEN];
    let _ = unsafe {
        bpf_probe_read_user_str_bytes(args.filename as *const u8, &mut filename_buf)
            .map_err(|_| 0u32)?
    };
    may_stage_to_tmp(&ctx, filename_buf);
    Ok(0)
}

//
// Enter openat()
//

// From /sys/kernel/debug/tracing/events/syscalls/sys_enter_openat/format
#[repr(C)]
struct SysEnterOpenat {
    // Common inaccessible fields
    common_type: u16,
    common_flags: u8,
    common_preempt_count: u8,
    common_pid: i32,

    // syscall-specific fields
    __syscall_nr: i32,
    dfd: u64,
    filename: u64, // pointer to filename
    flags: u64,
    mode: u64,
}

#[tracepoint]
pub fn enter_openat(ctx: TracePointContext) -> u32 {
    match handle_enter_openat(ctx) {
        Ok(ret) => ret,
        Err(ret) => ret,
    }
}

fn handle_enter_openat(ctx: TracePointContext) -> Result<u32, u32> {
    let args: SysEnterOpenat = unsafe {
        match ctx.read_at(0) {
            Ok(r) => r,
            Err(err) => {
                error!(&ctx, "Failed to get args in enter_openat(): error #{}", err);
                return Ok(0);
            }
        }
    };
    let mut filename_buf = [0u8; BUF_PATH_LEN];
    let _ = unsafe {
        bpf_probe_read_user_str_bytes(args.filename as *const u8, &mut filename_buf)
            .map_err(|_| 0u32)?
    };
    may_stage_to_tmp(&ctx, filename_buf);
    Ok(0)
}

//
// Enter openat2()
//

// From /sys/kernel/debug/tracing/events/syscalls/sys_enter_openat2/format
#[repr(C)]
struct SysEnterOpenat2 {
    // Common inaccessible fields
    common_type: u16,
    common_flags: u8,
    common_preempt_count: u8,
    common_pid: i32,

    // syscall-specific fields
    __syscall_nr: i32,
    dfd: u64,
    filename: u64, // pointer to filename
    open_how: u64,
    size_t: u64,
}

#[tracepoint]
pub fn enter_openat2(ctx: TracePointContext) -> u32 {
    match handle_enter_openat2(ctx) {
        Ok(ret) => ret,
        Err(ret) => ret,
    }
}

fn handle_enter_openat2(ctx: TracePointContext) -> Result<u32, u32> {
    let args: SysEnterOpenat2 = unsafe {
        match ctx.read_at(0) {
            Ok(r) => r,
            Err(err) => {
                error!(
                    &ctx,
                    "Failed to get args in enter_openat2(): error #{}", err
                );
                return Ok(0);
            }
        }
    };
    let mut filename_buf = [0u8; BUF_PATH_LEN];
    let _ = unsafe {
        bpf_probe_read_user_str_bytes(args.filename as *const u8, &mut filename_buf)
            .map_err(|_| 0u32)?
    };
    may_stage_to_tmp(&ctx, filename_buf);
    Ok(0)
}

//
// Exit openat() and openat2()
//

// From /sys/kernel/debug/tracing/events/syscalls/sys_exit_open/format
// and /sys/kernel/debug/tracing/events/syscalls/sys_exit_openat/format
// and /sys/kernel/debug/tracing/events/syscalls/sys_exit_openat2/format
#[repr(C)]
struct SysExitOpenat {
    // Common inaccessible fields
    common_type: u16,
    common_flags: u8,
    common_preempt_count: u8,
    common_pid: i32,

    // syscall-specific fields
    __syscall_nr: i32,
    ret: i64, // file descriptor
}

#[tracepoint]
pub fn exit_open(ctx: TracePointContext) -> u32 {
    match handle_exit_open(ctx) {
        Ok(ret) => ret,
        Err(ret) => ret,
    }
}

#[tracepoint]
pub fn exit_openat(ctx: TracePointContext) -> u32 {
    match handle_exit_open(ctx) {
        Ok(ret) => ret,
        Err(ret) => ret,
    }
}

#[tracepoint]
pub fn exit_openat2(ctx: TracePointContext) -> u32 {
    match handle_exit_open(ctx) {
        Ok(ret) => ret,
        Err(ret) => ret,
    }
}

fn handle_exit_open(ctx: TracePointContext) -> Result<u32, u32> {
    let args: SysExitOpenat = unsafe {
        match ctx.read_at(0) {
            Ok(r) => r,
            Err(err) => {
                error!(&ctx, "Failed to get args in exit_openat(): error #{}", err);
                return Ok(0);
            }
        }
    };
    let tgid = bpf_get_current_pid_tgid() >> 32;
    unsafe {
        if let Some(f) = TMP.get(tgid) {
            if args.ret >= 0 {
                let akey = ActiveKey {
                    _tgid: tgid,
                    _fd: args.ret as u64,
                };
                match ACTIVE.insert(akey, f, 0) {
                    Ok(_) => match PROC.get(tgid) {
                        Some(data) => {
                            let mut ndata: [u64; PROC_OPENED_FD_LEN] = [0u64; PROC_OPENED_FD_LEN];
                            let mut added = false;
                            for idx in 0..PROC_OPENED_FD_LEN {
                                match data[idx] {
                                    0 => {
                                        ndata[idx] = args.ret as u64;
                                        added = true;
                                        if let Err(err) = PROC.insert(tgid, ndata, 0) {
                                            error!(
                                                &ctx,
                                                "Failed to update to PROC map: error #{}", err
                                            );
                                        }
                                        break;
                                    }
                                    _ => ndata[idx] = data[idx],
                                }
                            }
                            if !added {
                                error!(&ctx, "Not enough room to update PROC for PID", tgid)
                            }
                        }
                        None => {
                            let mut data = [0u64; PROC_OPENED_FD_LEN];
                            data[0] = args.ret as u64;
                            if let Err(err) = PROC.insert(tgid, data, 0) {
                                error!(&ctx, "Failed to add to PROC map: error #{}", err);
                            }
                        }
                    },
                    Err(err) => {
                        error!(&ctx, "Failed to add to ACTIVE map: error #{}", err);
                    }
                }

                let uid = bpf_get_current_uid_gid() as u32;
                if let Err(err) =
                    send_event_to_userspace(EventType::OPEN, uid, tgid, f, args.ret as u64)
                {
                    error!(
                        &ctx,
                        "Failed to send OPEN event to userspace: error #{}", err
                    );
                }
            }
            if let Err(err) = TMP.remove(tgid) {
                error!(
                    &ctx,
                    "Failed to remove entry in TMP for PID {}: error #{}", tgid, err
                );
            }
        }
    }
    Ok(0)
}

//
// Enter close()
//

// From /sys/kernel/debug/tracing/events/syscalls/sys_enter_close/format
#[repr(C)]
struct SysEnterClose {
    // Common inaccessible fields
    common_type: u16,
    common_flags: u8,
    common_preempt_count: u8,
    common_pid: i32,

    // syscall-specific fields
    __syscall_nr: i32,
    fd: u64,
}

#[tracepoint]
pub fn enter_close(ctx: TracePointContext) -> u32 {
    match handle_enter_close(ctx) {
        Ok(ret) => ret,
        Err(ret) => ret,
    }
}

fn handle_enter_close(ctx: TracePointContext) -> Result<u32, u32> {
    let args: SysEnterClose = unsafe {
        match ctx.read_at(0) {
            Ok(r) => r,
            Err(err) => {
                error!(&ctx, "Failed to get args in enter_close(): error #{}", err);
                return Ok(0);
            }
        }
    };
    let tgid = bpf_get_current_pid_tgid() >> 32;
    unsafe {
        let akey = ActiveKey {
            _tgid: tgid,
            _fd: args.fd,
        };
        if let Some(f) = ACTIVE.get(&akey) {
            let uid = bpf_get_current_uid_gid() as u32;
            if let Err(err) = send_event_to_userspace(EventType::CLOSE, uid, tgid, f, args.fd) {
                error!(
                    &ctx,
                    "Failed to send CLOSE event to userspace: error #{}", err
                );
            }

            match ACTIVE.remove(&akey) {
                Ok(_) => match PROC.get(tgid) {
                    Some(data) => {
                        let mut ndata: [u64; PROC_OPENED_FD_LEN] = [0u64; PROC_OPENED_FD_LEN];
                        let mut removed = false;
                        for idx in 0..PROC_OPENED_FD_LEN {
                            if data[idx] == args.fd {
                                ndata[idx] = 0;
                                removed = true;
                                if let Err(err) = PROC.insert(tgid, ndata, 0) {
                                    error!(&ctx, "Failed to update to PROC map: error #{}", err);
                                }
                                break;
                            } else {
                                ndata[idx] = data[idx]
                            }
                        }
                        if !removed {
                            error!(
                                &ctx,
                                "Invalid PROC for PID {}, missing FD {}", tgid, args.fd
                            )
                        }
                    }
                    None => {
                        error!(&ctx, "Missing PROC for PID", tgid)
                    }
                },
                Err(err) => {
                    error!(
                        &ctx,
                        "Failed to remove entry in ACTIVE for PID {}: error #{}", tgid, err
                    );
                }
            }
        }
    }
    Ok(0)
}

//
// sched_process_exit()
//

// From /sys/kernel/debug/tracing/events/sched/sched_process_exit/format
#[repr(C)]
struct SchedProcessExit {
    // Common inaccessible fields
    common_type: u16,
    common_flags: u8,
    common_preempt_count: u8,
    common_pid: i32,

    // specific fields
    comm: [u8; 16],
    pid: i32,
    prio: i32,
}

#[tracepoint]
pub fn sched_process_exit(ctx: TracePointContext) -> u32 {
    match handle_process_exit(ctx) {
        Ok(ret) => ret,
        Err(ret) => ret,
    }
}

fn handle_process_exit(ctx: TracePointContext) -> Result<u32, u32> {
    let args: SchedProcessExit = unsafe {
        match ctx.read_at(0) {
            Ok(r) => r,
            Err(err) => {
                error!(&ctx, "Failed to get args in enter_close(): error #{}", err);
                return Ok(0);
            }
        }
    };
    if args.pid < 0 {
        error!(
            &ctx,
            "sched_process_exit reports a negative PID {}", args.pid
        );
        return Ok(0);
    }
    unsafe {
        let tgid = args.pid as u64;
        if let Some(data) = PROC.get(tgid) {
            let uid = bpf_get_current_uid_gid() as u32;
            for idx in 0..PROC_OPENED_FD_LEN {
                if data[idx] != 0 {
                    let akey = ActiveKey {
                        _tgid: tgid,
                        _fd: data[idx],
                    };
                    match ACTIVE.get(&akey) {
                        Some(f) => {
                            if let Err(err) =
                                send_event_to_userspace(EventType::CLOSE, uid, tgid, f, data[idx])
                            {
                                error!(
                                    &ctx,
                                    "Failed to send CLOSE event to userspace: error #{}", err
                                );
                            }
                            if let Err(err) = ACTIVE.remove(&akey) {
                                error!(
                                    &ctx,
                                    "Failed to remove entry in ACTIVE for PID {}: error #{}",
                                    tgid,
                                    err
                                );
                            }
                        }
                        None => {
                            error!(
                                &ctx,
                                "Incoherence between ACTIVE and PROC for PID {} and FD {}",
                                tgid,
                                data[idx]
                            )
                        }
                    }
                }
            }

            if let Err(err) = PROC.remove(tgid) {
                error!(
                    &ctx,
                    "Failed to remove entry in PROC for PID {}: error #{}", tgid, err
                );
            }
        }
    }

    Ok(0)
}

//
// Misc.
//

fn may_stage_to_tmp(ctx: &TracePointContext, filename: [u8; BUF_PATH_LEN]) {
    if filename.starts_with(USB_PATH_PREFIX) {
        let tgid = bpf_get_current_pid_tgid() >> 32;
        if let Some(_f) = unsafe { TMP.get(tgid) } {
            warn!(ctx, "Already something in TMP for PID {}", tgid)
        }
        if let Err(err) = TMP.insert(tgid, filename, 0) {
            error!(ctx, "Failed to add to TMP map: error #{}", err)
        }
    }
}

fn send_event_to_userspace(
    ev_type: EventType,
    uid: u32,
    tgid: u64,
    path_buf: &[u8; BUF_PATH_LEN],
    fd: u64,
) -> Result<(), u32> {
    // get associated command
    let comm_r = bpf_get_current_comm();
    let comm = match &comm_r {
        Ok(c) => *c,
        Err(_) => [0u8; 16],
    };
    let mut entry = EVENTS.reserve::<Event>(0).ok_or(1u32)?;
    entry.write(Event {
        uid,
        pid: tgid as u32,
        ev_type,
        file: *path_buf,
        comm,
        fd,
    });
    entry.submit(0);
    Ok(())
}

#[cfg(not(test))]
#[panic_handler]
fn panic(_info: &core::panic::PanicInfo) -> ! {
    loop {}
}

#[unsafe(link_section = "license")]
#[unsafe(no_mangle)]
static LICENSE: [u8; 13] = *b"Dual MIT/GPL\0";
