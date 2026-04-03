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

use aya_ebpf::helpers::bpf_d_path;
use aya_ebpf::helpers::bpf_get_current_comm;
use aya_ebpf::helpers::bpf_get_current_pid_tgid;
use aya_ebpf::helpers::bpf_get_current_task;
use aya_ebpf::helpers::bpf_get_current_uid_gid;
use aya_ebpf::helpers::{bpf_probe_read_kernel, bpf_ktime_get_ns, bpf_probe_read_kernel_str_bytes};
use aya_ebpf::{
    macros::{lsm, map},
    maps::{PerCpuArray, HashMap, RingBuf},
    programs::LsmContext
};
use aya_log_ebpf::{warn, error};

use data_access_guard_common::{KEY_INIT_NETNS, KEY_PADSI_PID, BUF_PATH_LEN, Event, BlockedCall};
mod vmlinux;

const PADSI_PROTECTED_USER: &[u8] = "/run/padsi/user/".as_bytes();
const PADSI_PROTECTED_LOG: &[u8] = "/var/padsi/log/".as_bytes();
const MAX_PATH_LEN: usize=4096;

#[allow(
    clippy::all,
    dead_code,
    improper_ctypes_definitions,
    non_camel_case_types,
    non_snake_case,
    non_upper_case_globals,
    unnecessary_transmutes,
    unsafe_op_in_unsafe_fn,
)]
#[rustfmt::skip]

// Per-CPU scratch buffer to avoid stack overflow while getting path
#[map]
static mut PATH_BUFFER: PerCpuArray<[u8; MAX_PATH_LEN]> = PerCpuArray::with_max_entries(1, 0);

#[map(name = "CONFIG")]
static CONFIG: HashMap<u8, u64> = HashMap::<u8, u64>::with_max_entries(8, 0);

// RingBuf map to send events to userspace
#[map]
static EVENTS: RingBuf = RingBuf::with_byte_size(16 * 4096, 0);

#[lsm(hook = "file_open")]
pub fn file_open(ctx: LsmContext) -> i32 {
    match try_file_open(ctx) {
        Ok(_) => 0,
        Err(ret) => ret,
    }
}

#[lsm(hook = "path_unlink")]
pub fn path_unlink(ctx: LsmContext) -> i32 {
    match try_path_unkink(ctx) {
        Ok(_) => 0,
        Err(ret) => ret,
    }
}

/// check if we may block based oh the file/directory's path
fn may_block_from_path(ctx: &LsmContext, path_ptr: *const vmlinux::path) -> Result<(bool, Option<(*mut [u8;4096], i64)>), i32> {
    // first quick filter to determine if we may block
    let may_block=is_padsi_subdir(path_ptr).map_or_else(|_| true, |v| v); // pre filter
    if ! may_block {
        return Ok((false, None))
    }

    // get the actual path, should always succeed because we have 4096 bytes left
    // get the per-CPU buffer
    let path_buf = unsafe {
        let ptr = core::ptr::addr_of_mut!(PATH_BUFFER);
        (*ptr).get_ptr_mut(0).ok_or(0)?
    };
    let path_len = unsafe {
        bpf_d_path(
            path_ptr as *const vmlinux::path as *mut aya_ebpf::bindings::path,
            path_buf as *mut i8,
            MAX_PATH_LEN as u32,
        )
    };
    if path_len < 0 {
        warn!(&ctx, "access blocked: path was too long or there was an error trying to get it");
        return Err(-1);
    }

    // tests based on the whole path
    unsafe {
        match (*path_buf).starts_with(PADSI_PROTECTED_USER) || (*path_buf).starts_with(PADSI_PROTECTED_LOG) {
            true => Ok((true, Some((path_buf, path_len)))),
            false => Ok((false, None))
        }
    }
}

fn must_block_on_pid(ctx: &LsmContext) -> Result<bool, i32> {
    let mut task_ptr = unsafe { bpf_get_current_task() as *const vmlinux::task_struct };
    if task_ptr.is_null() {
        warn!(&ctx, "access denied: task_ptr is NULL!");
        return Err(-1);
    }

    // get allowed PPID
    let exp_ppid= unsafe {
        match CONFIG.get(&KEY_PADSI_PID) {
            Some(v) => *v,
            None => {
                warn!(&ctx, "access denied: CONFIG map does not contain the padsi's system service PID (code bug)");
                return Err(-1)
            }
        }
    };

    // check if process is allowed because it is a child (direct or not of PADSI's system service)
    for level in 0..5 {
        // weird error while loading eBPF program if the level variable is not actually used (probably an optimization the verifier does not like)
        if level<0 {
            return Err(-1)
        }
        let (ppid, ptask_ptr)=match get_ppid(task_ptr) {
            Ok(v) => v,
            Err(e) => return Err(e)
        };
        task_ptr=ptask_ptr;
        if ppid as u32==exp_ppid as u32 {
            return Ok(false);
        }
    }

    // check namespaces
    let net_ns = get_net_ns(task_ptr)?;
    let init_net_ns= unsafe {
        match CONFIG.get(&KEY_INIT_NETNS) {
            Some(v) => *v,
            None => {
                warn!(&ctx, "access denied: CONFIG map does not contain the init net NS (code bug)");
                return Err(-1)
            }
        }
    };
    if net_ns!=init_net_ns {
        return Ok(false)
    }
    Ok(true)
}

fn send_event_to_userspace(call_type: BlockedCall, path_buf: *mut [u8;4096], path_len: i64) -> Result<(), i32>{
    // log access denied via the ring buffer
    let mut entry = EVENTS
        .reserve::<Event>(0)
        .ok_or(-1)?;

    // get associated command
    let comm_r = bpf_get_current_comm();
    let comm = match &comm_r {
        Ok(c) => *c,
        Err(_) => [0u8; 16]
    };

    let uid = bpf_get_current_uid_gid() as u32;
    let tgid = (bpf_get_current_pid_tgid() >> 32) as u32;
    let timestamp = unsafe { bpf_ktime_get_ns() };
    let mut file=[0u8; BUF_PATH_LEN];
    let l=match path_len as usize>=BUF_PATH_LEN{
        true => BUF_PATH_LEN,
        false => path_len as usize
    };
    unsafe {
        core::ptr::copy_nonoverlapping((*path_buf).as_ptr(), file.as_mut_ptr(), l);
    }
    entry.write(Event {
        call_type,
        pid: tgid,
        uid,
        comm,
        timestamp,
        file
    });
    entry.submit(0);
    Ok(())
}

fn try_file_open(ctx: LsmContext) -> Result<i32, i32> {
    // system accounts are always allowed
    let uid = bpf_get_current_uid_gid() as u32;
    if uid < 1000 {
        return Ok(0);
    }

    // get pointer to the struct file
    let file_ptr: *const vmlinux::file = ctx.arg(0);
    if file_ptr.is_null() {
        warn!(&ctx, "access denied: file_ptr is NULL!");
        return Err(-1);
    }

    // first test based on the file's path, and get back the file's path;
    let path_ptr: *const vmlinux::path = unsafe { &(*file_ptr).f_path }; // the addr_of! macro is deprecated: core::ptr::addr_of!((*file_ptr).f_path)
    let (path_buf, path_len)=match may_block_from_path(&ctx, path_ptr) {
        Ok((false, _)) => return Ok(0),
        Ok((true, Some(v))) => v,
        Ok((true, None)) => {
            error!(&ctx, "codebug: may_block_from_path() returned True but without path and len!");
            return Err(-1)
        }
        Err(v) => return Err(v),
    };

    unsafe {
        if (*path_buf).starts_with(PADSI_PROTECTED_LOG) {
            let flags:u32= (*file_ptr).f_flags;
            if flags & 0x3==0 {
                return Ok(0)
            }
        }
    }

    // test using the PID
    match must_block_on_pid(&ctx) {
        Ok(false) => return Ok(0),
        Err(e) => return Err(e),
        _ => {}
    }

    // send blocked event
    if send_event_to_userspace(BlockedCall::FileOpen, path_buf, path_len).is_err() {
        error!(&ctx, "Failed to send event to userspace")
    }
    return Err(-1);
}

fn try_path_unkink(ctx: LsmContext) -> Result<i32, i32> {
    // system accounts are always allowed
    let uid = bpf_get_current_uid_gid() as u32;
    if uid < 1000 {
        return Ok(0);
    }

    // get pointer to the struct file
    let dir_ptr: *const vmlinux::path = ctx.arg(0);
    if dir_ptr.is_null() {
        warn!(&ctx, "access denied: dir_ptr is NULL!");
        return Err(-1);
    }

    // first test based on the file's path, and get back the file's path
    let (path_buf, path_len)=match may_block_from_path(&ctx, dir_ptr) {
        Ok((false, _)) => return Ok(0),
        Ok((true, Some(v))) => v,
        Ok((true, None)) => {
            error!(&ctx, "codebug: may_block_from_path() returned True but without path and len!");
            return Err(-1)
        }
        Err(v) => return Err(v),
    };

    // test using the PID
    match must_block_on_pid(&ctx) {
        Ok(false) => return Ok(0),
        Err(e) => return Err(e),
        _ => {}
    }

    // send blocked event
    if send_event_to_userspace(BlockedCall::PathUnlink, path_buf, path_len).is_err() {
        error!(&ctx, "Failed to send event to userspace")
    }
    return Err(-1)
}

const PADSI_STR:&str="padsi";
const NB_PATHS:usize=5;
///
/// Tell if the path has a "padsi" part
/// Returns an Err if the path is too deep
///
fn is_padsi_subdir(kpath: *const vmlinux::path) -> Result<bool, i32> {
    let mut circular_buffer: [Option<*const u8>; NB_PATHS]=[None; NB_PATHS]; // array of qstr (null terminated C strings)
    let mut dtry= unsafe {(*kpath).dentry};
    for depth in 0..128 {
        let circular_index=depth%NB_PATHS;
        circular_buffer[circular_index]=unsafe {Some((*dtry).d_name.name)};
        let parent=unsafe {(*dtry).d_parent};
        if parent==dtry {
            // no more parent => we are done (#define IS_ROOT(x) ((x) == (x)->d_parent))
            for i in 0..NB_PATHS {
                match circular_buffer[i] {
                    Some(name_ptr) => {
                        let mut data=[0u8; PADSI_STR.len()+1];
                        let name=unsafe{str::from_utf8_unchecked(bpf_probe_read_kernel_str_bytes(name_ptr, &mut data[..]).map_err(|_| -1i32)?)};
                        if name=="padsi" {
                            return Ok(true)
                        }
                    }
                    None => return Ok(false)
                }
            }
            return Ok(false)
        } else {
            dtry=parent;
        }
    }
    return Err(-1)
}

fn get_ppid(task_ptr: *const vmlinux::task_struct) -> Result<(i32, *const vmlinux::task_struct), i32> {
    unsafe {
        match bpf_probe_read_kernel(&(*task_ptr).real_parent) {
            Ok(ptask_ptr) => {
                match bpf_probe_read_kernel(&(*ptask_ptr).tgid) {
                    Ok(tgid) => Ok((tgid, ptask_ptr)),
                    Err(_) => return Err(-1)
                }
            },
            Err(_) => return Err(-1)
        }
    }
}

fn get_net_ns(task_ptr: *const vmlinux::task_struct) -> Result<u64, i32> {
    // get the namespaces
    let nsproxy: *const vmlinux::nsproxy = unsafe {
        // FAILS: let nsproxy: *const vmlinux::nsproxy = unsafe { (*task_ptr).nsproxy };
        match bpf_probe_read_kernel(&(*task_ptr).nsproxy) {
            Ok(v) => v,
            Err(_) => return Err(-1),
        }
    };
    let ns_ptr: *const vmlinux::net = unsafe {
        match bpf_probe_read_kernel(&(*nsproxy).net_ns) {
            Ok(v) => v,
            Err(_) => return Err(-1),
        }
    };
    unsafe {
        match bpf_probe_read_kernel(&(*ns_ptr).ns) {
            Ok(v) => Ok(v.inum as u64),
            Err(_) => Err(-1),
        }
    }
}

#[cfg(not(test))]
#[panic_handler]
fn panic(_info: &core::panic::PanicInfo) -> ! {
    loop {}
}

#[unsafe(link_section = "license")]
#[unsafe(no_mangle)]
static LICENSE: [u8; 13] = *b"Dual MIT/GPL\0";
