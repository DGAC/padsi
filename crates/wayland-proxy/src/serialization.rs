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

use crate::HEADER_SIZE;


/// Structure for a Wayland Message header
///
/// A Wayland Message header contains three atributes: the Object_ID, the message_size and the Opcode.
/// The Object_ID is 4 bytes long, which correponds to a u32. While the message_size and Opcode are
/// 2 bytes long each, which corresponds to a u16.
#[derive(Clone, Debug, PartialEq)]
pub struct MsgHeader{
    pub object_id: u32,
    pub msg_size: u16,
    pub opcode: u16
}


impl MsgHeader{
    /// Creates a new blank header
    ///
    /// Creates a new blank header. All values (O_ID, msg_size and opcode) are initialised to 0.
    pub fn new() -> Self {
        Self{
            object_id: 0,
            msg_size: 0,
            opcode: 0,
        }
    }
}

/// Reads the first header of a buffer
///
/// Reads and returns the first message header in the passed buffer.
pub fn read_header(bytes: &[u8; HEADER_SIZE]) -> MsgHeader {
    let mut header = MsgHeader::new();
    header.object_id = u32::from_le_bytes(bytes[0..4].try_into().unwrap());
    header.msg_size = u16::from_le_bytes(bytes[6..8].try_into().unwrap());  // from 6 to 8 because word is in big endian
    header.opcode = u16::from_le_bytes(bytes[4..6].try_into().unwrap());    // from 4 to 6 because word is in big endian
    return header
}

/// Gets the next message start in buffer
///
/// Gets and returns the next message start in buffer by using the current header
/// (more precisely the current message size contained in the header).
pub fn get_next_msg_start(current_start: usize, current_header: &MsgHeader) -> usize {
    let next_msg_start: usize = current_start + current_header.msg_size as usize;
    next_msg_start
}
