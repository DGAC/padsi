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

use core::fmt::{self, Display};
pub const BUF_PATH_LEN: usize = 32; // should be more than enough for /dev/bus/usb/*/*

#[derive(Clone, Copy)]
pub enum EventType {
    OPEN,
    CLOSE,
}

impl Display for EventType {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::OPEN => write!(f, "OPEN"),
            Self::CLOSE => write!(f, "CLOSE"),
        }
    }
}

/// Event structure that will be sent from eBPF to userspace
#[repr(C)]
#[derive(Clone, Copy)]
pub struct Event {
    pub uid: u32,
    pub pid: u32,
    pub ev_type: EventType,
    pub file: [u8; BUF_PATH_LEN],
    pub comm: [u8; 16],
    pub fd: u64,
}
