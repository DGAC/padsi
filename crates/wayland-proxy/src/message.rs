use crate::config::is_little_endian;
use crate::constants::{HEADER_SIZE, WORD_SIZE};
use anyhow::{Result, anyhow};
use padsi::trace::warn;

type WlObjectId = u32; // instantiated object
type WlOpCode = u16; // method / event OpCode

/// Wayland Message's metadata
///
/// Contains information about a single Wayland message, without holding any reference to
/// the actual data, but instead only holding the index in the buffer and the size of the message
/// (header & payload)
#[derive(Clone, Debug, PartialEq)]
pub struct MessageMeta {
    pub index: usize, // index of the message's location in the buffer
    pub size: usize,  // message's total size (header & payload)
    pub object_id: u32,
    pub opcode: u16,
}

/// parse a Wayland message's header and return the Object ID, Opcode and message's length
pub fn parse_header(header_bytes: &[u8]) -> (WlObjectId, WlOpCode, usize) {
    let object_id = u32::from_ne_bytes(header_bytes[0..4].try_into().unwrap());
    let (msg_size, opcode) = if is_little_endian() {
        (
            u16::from_ne_bytes(header_bytes[6..8].try_into().unwrap()),
            u16::from_ne_bytes(header_bytes[4..6].try_into().unwrap()),
        )
    } else {
        (
            u16::from_ne_bytes(header_bytes[4..6].try_into().unwrap()),
            u16::from_ne_bytes(header_bytes[6..8].try_into().unwrap()),
        )
    };
    (object_id, opcode, msg_size as usize)
}

impl MessageMeta {
    /// Create a message from a buffer at a specified index
    pub fn from_data(buffer: &Vec<u8>, start_position: usize) -> Result<Self> {
        let blen = buffer.len();
        if blen < start_position + HEADER_SIZE {
            return Err(anyhow!("invalid message, can't contain a Wayland header"));
        }

        // parse message header
        let header_bytes = &buffer[start_position..start_position + HEADER_SIZE];
        let (object_id, opcode, msg_size) = parse_header(header_bytes);

        if blen < start_position + msg_size as usize {
            return Err(anyhow!("invalid buffer, no room to contain the payload"));
        }
        if (msg_size as usize) < HEADER_SIZE {
            return Err(anyhow!("invalid message, size inferior to header size"));
        }
        Ok(MessageMeta {
            index: start_position,
            size: msg_size as usize,
            object_id,
            opcode,
        })
    }

    pub fn len(&self) -> usize {
        self.size
    }

    pub fn payload<'a>(&self, buffer: &'a Vec<u8>) -> &'a [u8] {
        &buffer[self.index + HEADER_SIZE..self.index + self.size]
    }

    /// Extract the object ID from a message's payload, expected to be the last 4 bytes
    /// of the message, or the last bytes before the specified offset in bytes
    pub fn extract_referenced_object_id(&self, buffer: &Vec<u8>, end_offset: usize) -> Option<u32> {
        let payload = self.payload(buffer);
        let payload_length = payload.len();
        if payload_length - end_offset > payload_length {
            return None;
        }
        let data = payload[payload_length - end_offset - WORD_SIZE..payload_length - end_offset]
            .try_into();
        match data {
            Ok(d) => Some(u32::from_ne_bytes(d)),
            Err(err) => {
                warn!(
                    "could not extract object ID from message '{:?}' ({})",
                    payload,
                    err.to_string()
                );
                None
            }
        }
    }
}
