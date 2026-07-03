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

use core::fmt::{Display, Formatter, Result};

pub const KEY_INIT_NETNS: u8 = 1;
pub const KEY_PADSI_PID: u8 = 2;
pub const BUF_PATH_LEN: usize = 320;

#[derive(Clone, Copy)]
pub enum BlockedCall {
    FileOpen,
    PathUnlink,
}

impl Display for BlockedCall {
    fn fmt(&self, f: &mut Formatter<'_>) -> Result {
        match self {
            BlockedCall::FileOpen => write!(f, "file_open"),
            BlockedCall::PathUnlink => write!(f, "file_unlink"),
        }
    }
}

/// Event structure that will be sent from eBPF to userspace
#[repr(C)]
#[derive(Clone, Copy)]
pub struct Event {
    pub call_type: BlockedCall,
    pub pid: u32,
    pub uid: u32,
    pub comm: [u8; 16], // process name
    pub timestamp: u64,
    pub file: [u8; BUF_PATH_LEN],
}
