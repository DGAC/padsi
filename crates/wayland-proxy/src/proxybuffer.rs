use std::cell::RefCell;
use std::rc::Rc;
use std::usize;

use padsi::trace::{debug, error, trace};

use crate::config::is_little_endian;
use crate::constants::{self, HEADER_SIZE};
use crate::filter::ProxyState;
use crate::message::MessageMeta;

/// Data to work on
/// The data is a mutable reference, not owned
pub struct ProxyBuffer<'a> {
    data: &'a mut Vec<u8>,                           // raw data, may be modified
    size: usize, // actual size to consider (.data will be much larger)
    messages: Rc<RefCell<Option<Vec<MessageMeta>>>>, // .data analysed into messages (computed when necessary)
}

impl<'a> ProxyBuffer<'a> {
    pub fn from_data(data: &'a mut Vec<u8>, size: usize) -> Self {
        Self {
            data,
            size,
            messages: Rc::new(RefCell::new(None)),
        }
    }

    #[cfg(test)]
    pub fn messages(&self) -> Vec<MessageMeta> {
        self.ensure_parsed();
        let opt_msgs = self.messages.borrow();
        match &*opt_msgs {
            Some(msgs) => msgs.clone(),
            None => panic!("CODEBUG: opt_msgs should not be None"),
        }
    }

    /// Current size of the buffer
    pub fn len(&self) -> usize {
        self.size
    }

    /// Ensure self.data has been split into messages
    fn ensure_parsed(&self) {
        let mut opt_msgs = self.messages.borrow_mut();
        if let None = *opt_msgs {
            *opt_msgs = Some(match parse_buffer(self.data, self.size) {
                Some(msgs) => msgs,
                None => vec![],
            });
        }
    }

    #[allow(dead_code)]
    pub fn debug(&self, context: &str) {
        self.ensure_parsed();
        let opt_msgs = self.messages.borrow();
        match &*opt_msgs {
            Some(msgs) => self.debug_messages(msgs, context),
            None => panic!("CODEBUG: opt_msgs should not be None"),
        }
    }

    pub fn trace(&self, context: &str) {
        self.ensure_parsed();
        let opt_msgs = self.messages.borrow();
        match &*opt_msgs {
            Some(msgs) => self.trace_messages(msgs, context),
            None => panic!("CODEBUG: opt_msgs should not be None"),
        }
    }

    /// Search for a message
    ///
    /// Searches for a message containing the passed Object_ID, Opcode and payload text in the passed Vector of messages.
    /// Disregards the Object_ID argument if it is set to `0`. Disregards text to search in the payload if it is set to `""`.
    /// The payload text passed in the arguments does not need to be the exact same as the one in the message, because this
    /// function uses the 'contains' method.
    pub fn search(
        &self,
        obj_id: Option<u32>,
        opcode: u16,
        text: Option<&str>,
    ) -> Option<MessageMeta> {
        self.ensure_parsed();
        let opt_msgs = self.messages.borrow();
        match &*opt_msgs {
            Some(msgs) => search_in_messages_in_vec(self.data, msgs, obj_id, opcode, text),
            None => panic!("CODEBUG: opt_msgs should not be None"),
        }
    }

    /// Search for a referenced object ID in a message for which the search criteria are specified
    pub fn search_referenced_object_id(
        &self,
        obj_id: Option<u32>,
        opcode: u16,
        interface: Option<&str>,
        end_offset: usize,
    ) -> Option<u32> {
        match self.search(obj_id, opcode, interface) {
            Some(msg) => {
                //msg.debug("Found searched message");
                msg.extract_referenced_object_id(self.data, end_offset)
            }
            None => None,
        }
    }

    /// Inserts zone ID as a new mime type message
    pub fn insert_zone_name_tag(
        &mut self,
        raw_position: usize,
        data_source_oid: u32,
        zone_name: &str,
    ) {
        let new_msg = create_zone_raw_message(data_source_oid, zone_name);

        // insert new message into the data
        self.size += new_msg.len();
        self.data.splice(raw_position..raw_position, new_msg);

        // clean up parsed messages as the list is obsolete
        let mut opt_msgs = self.messages.borrow_mut();
        *opt_msgs = None;

        if let Ok(inserted_msg) = MessageMeta::from_data(&self.data, raw_position) {
            self.debug_a_message(&inserted_msg, "zone tag message")
        }
    }

    /// Try to get the zone name
    pub fn search_zone_name_tag(&self, oid: u32) -> Option<String> {
        match self.search(Some(oid), constants::DO_E_OFFER, Some(MIME_TYPE_PREFIX)) {
            Some(zone_msg) => get_zone_tag(zone_msg.payload(self.data)),
            None => None,
        }
    }

    /// Remove some messages
    ///
    /// Note that all the messages' indexes must be valid (i.e. they must have been computed after any modifications
    /// made to the buffer if any).
    ///
    /// Also, the messages must be ordered in the buffer.
    pub fn remove_messages(&mut self, mut msgs: Vec<MessageMeta>) {
        msgs.reverse();
        let mut last_pos: usize = usize::MAX;
        for msg in &msgs {
            // check that messages are ordered so that we can start removing tha last message and moving towards the first
            // while always keeping each message's position valid
            if msg.index > last_pos {
                self.debug_messages(&msgs, "remove_messages called (in reverse order)");
                panic!("messages passed to remove_messages() are out of order")
            }
            last_pos = msg.index;

            // remove the message
            self.data.splice(msg.index..msg.index + msg.len(), []);
            self.size -= msg.len()
        }

        // clean up parsed messages as the list is obsolete
        let mut opt_msgs = self.messages.borrow_mut();
        *opt_msgs = None;
    }

    /// Get messages which correspond to an object present in the filter context
    pub fn matching_messages(&self, proxy_state: &ProxyState) -> Vec<MessageMeta> {
        self.ensure_parsed();
        let opt_msgs = self.messages.borrow();
        match &*opt_msgs {
            Some(msgs) => proxy_state.get_matching_messages(msgs),
            None => panic!("CODEBUG: opt_msgs should not be None"),
        }
    }

    pub fn debug_a_message(&self, msg: &MessageMeta, context: &str) {
        debug!(
            object_id = msg.object_id,
            opcode = msg.opcode,
            msg_size = msg.size,
            start_byte = msg.index,
            payload = bytes_to_string(msg.payload(self.data)),
            "{}",
            context
        );
    }

    pub fn trace_a_message(&self, msg: &MessageMeta, context: &str) {
        trace!(
            object_id = msg.object_id,
            opcode = msg.opcode,
            msg_size = msg.size,
            start_byte = msg.index,
            payload = bytes_to_string(msg.payload(self.data)),
            "{}",
            context
        );
    }

    pub fn debug_messages(&self, msgs: &Vec<MessageMeta>, context: &str) {
        let mut counter = 0;
        for msg in msgs {
            self.debug_a_message(msg, &format!("{} ({})", context, counter));
            counter += 1
        }
    }

    fn trace_messages(&self, msgs: &Vec<MessageMeta>, context: &str) {
        let mut counter = 0;
        for msg in msgs {
            self.trace_a_message(msg, &format!("{} ({})", context, counter));
            counter += 1
        }
    }
}

/// Performs a light Wayland messages delimitation
pub fn parse_buffer(data: &Vec<u8>, size: usize) -> Option<Vec<MessageMeta>> {
    if size >= HEADER_SIZE {
        let mut msgs: Vec<MessageMeta> = vec![];
        let mut next_msg_start = 0;
        while next_msg_start < size {
            match MessageMeta::from_data(&data, next_msg_start) {
                Ok(msg) => {
                    next_msg_start += msg.size;
                    msgs.push(msg);
                }
                Err(err) => {
                    error!("{}", err.to_string());
                    break;
                }
            }
        }
        return Some(msgs);
    }
    None
}

pub fn search_in_messages_in_vec(
    data: &Vec<u8>,
    msgs: &Vec<MessageMeta>,
    obj_id: Option<u32>,
    opcode: u16,
    text: Option<&str>,
) -> Option<MessageMeta> {
    for msg in msgs {
        if (obj_id == None) || (msg.object_id == obj_id.unwrap()) {
            if msg.opcode == opcode {
                match text {
                    Some(t) => {
                        if let Some(text_payload) = bytes_to_str(msg.payload(data)) {
                            if text_payload.contains(t) {
                                return Some(msg.clone());
                            };
                        }
                    }
                    None => return Some(msg.clone()),
                }
            };
        }
    }
    None
}

/// Tries to convert the given payload to UTF-8
pub fn bytes_to_str(payload: &[u8]) -> Option<&str> {
    match std::str::from_utf8(payload) {
        Ok(text) => Some(text),
        Err(_) => None,
    }
}

const MIME_TYPE_PREFIX: &str = "padsi/zone;z=";

/// Create a new message
pub fn create_zone_raw_message(data_source_oid: u32, zone_name: &str) -> Vec<u8> {
    // Refer to Wayland's wire format: https://wayland.freedesktop.org/docs/book/Protocol.html#wire-format
    // a string in Wayland wire format: Starts with an unsigned 32-bit length (including null terminator),
    // followed by the UTF-8 encoded string contents, including terminating null byte, then padding to a 32-bit boundary.
    // A null value is represented with a length of 0. Interior null bytes are not permitted.
    let mut new_msg: Vec<u8> = vec![0; 12];
    let new_id_bytes: Vec<u8> = u32::to_ne_bytes(data_source_oid).to_vec();
    new_msg.splice(0..4, new_id_bytes);
    let opcode_bytes: Vec<u8> = u16::to_ne_bytes(constants::DS_R_OFFER).to_vec();

    if is_little_endian() {
        new_msg.splice(4..6, opcode_bytes);
    } else {
        new_msg.splice(6..8, opcode_bytes);
    }

    let mut data: Vec<u8> = MIME_TYPE_PREFIX.as_bytes().into();
    data.append(&mut zone_name.as_bytes().to_vec());
    //let mut data: Vec<u8>="text/plain;charset=utf-8".as_bytes().into();
    data.push(0);
    let data_len = data.len();
    for _ in 0..(4 - data_len % 4) {
        data.push(0);
    }
    new_msg.append(&mut data);

    let string_size_bytes: Vec<u8> = u32::to_ne_bytes(data_len as u32).to_vec();
    new_msg.splice(8..12, string_size_bytes);

    let msg_size = new_msg.len() as u16;
    let new_size_bytes: Vec<u8> = u16::to_ne_bytes(msg_size).to_vec();
    if is_little_endian() {
        new_msg.splice(6..8, new_size_bytes);
    } else {
        new_msg.splice(4..6, new_size_bytes);
    }
    new_msg
}

pub fn get_zone_tag(data: &[u8]) -> Option<String> {
    if let Some(str_payload) = bytes_to_str(data) {
        // Split payload str to get zone number
        let (_, data) = str_payload.split_at(4 + MIME_TYPE_PREFIX.len()); // 4 is the string's length
        let idx_split = data.find('\0').unwrap_or(data.len());
        let (zone_str, _) = data.split_at(idx_split);
        if zone_str.is_empty() {
            return None;
        }
        return Some(zone_str.to_string());
    }
    None
}

pub fn bytes_to_string(data: &[u8]) -> String {
    let mut res = String::new();
    let mut last_bin = false;
    for &b in data {
        if b.is_ascii_graphic() || b == b' ' {
            if last_bin {
                res.push(' ');
                last_bin = false
            }
            res.push(b as char);
        } else {
            if !last_bin {
                res.push(' ')
            }
            res.push_str(&format!("{:02X}", b));
            last_bin = true
        }
    }
    res
}
