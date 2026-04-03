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

use tokio::io::{AsyncReadExt, AsyncWriteExt};
use std::path::Path;
use std::env;
use asyncfd::UnixFdStream;
use std::os::unix::net::UnixStream as StdUnixStream;
use std::os::unix::net::UnixListener;
use std::collections::HashSet;
use std::time::{SystemTime, UNIX_EPOCH};
use padsi::trace::{LevelFilter, TraceConfig, error, info, tracing_setup_json, debug, trace};


use crate::serialization::*;
/// Serialisation module composed of all data structures and functions used to de-serialise Wayland headers.
mod serialization;

#[cfg(test)]
mod tests;

const BUFSIZE:usize=65536;                  // maximum message size in Wayland (from messages' headers structure)
const WORD_SIZE: usize = 4;                     // In Wayland, a word is 32 bits.
const HEADER_SIZE: usize = 2*WORD_SIZE;               // In Wayand, a message header is two 32 bit words, so 8 bytes total.
const MIME_TYPE_PREFIX: &str = "padsi/zone;z=";

// Constants for requests and events found in the wayland.xml
// wl_registry
const _RGSTRY_VERSION: u32 = 1;
const _RGSTRY_R_BIND: u16 = 0;

// wl_data_device_manager
const _DDM_VERSION: u32 = 3;
const DDM_R_CREATE_DS: u16 = 0;
const DDM_R_GET_DD: u16 = 1;

// wl_data_device
const _DD_VERSION: u32 = 3;
const DD_E_DO: u16 = 0;
const DD_R_DESTROY: u16 = 2;
const _DD_E_SEL: u16 = 5;

// wl_data_source
const _DS_VERSION: u32 = 3;
const DS_R_DESTROY: u16 = 1;
const DS_R_OFFER: u16 = 0;

// wl_data_offer
const _DO_VERSION: u32 = 3;
const DO_R_DESTROY: u16 = 2;
const DO_E_O: u16 = 0;

// zwp_primary_selection_device_manager_v1
const PS_DM_R_GET_PS_D: u16 = 1;
const _PS_DM_E_PS_O: u16 = 0;
const PS_DM_R_DESTROY: u16 = 2;

// zwp_primary_selection_device_v1
const PS_D_E_DO: u16 = 0;
const PS_D_R_DESTROY: u16 = 1;
const _PS_D_E_SEL: u16 = 1;

// zwp_primary_selection_offer_v1
const PS_O_R_DESTROY: u16 = 1;
const PS_O_E_O: u16 = 0;


/// Forwards all File Descriptors found in a stream
///
/// Queues each File Descriptor received in the `from_stream` to the `to_stream` to
/// be forwarded when the next message is sent through the `to_stream`. Uses the asyncfd crate.
async fn forward_all_fd(from_stream: &mut UnixFdStream<StdUnixStream>, to_stream: &mut UnixFdStream<StdUnixStream>) {
    loop {
        match from_stream.pop_incoming_fd() {
            Some(fd) => to_stream.push_outgoing_fd(fd),
            None => break,
        }
    }
}

/// Structure for a Wayland Message
///
/// A Wayland Message contains a header and a payload. This structure also adds a `start_in_buf` variable,
/// which represents the index of the first byte of the Message in the buffer of bytes.
#[derive(Clone, Debug, PartialEq)]
struct Message{
    header: MsgHeader,
    payload: Vec<u8>,
    start_in_buf: usize,
}


impl Message{
    /// Function to create a new blank Message
    ///
    /// This function is used to create a new Message, with an empty header and empty payload.
    pub fn new() -> Self {
        Self {
            header: (MsgHeader::new()),
            payload: (Vec::new()),
            start_in_buf: (0)
        }
    }
}


/// Reads a message from the buffer, using an index
///
/// This function returns a message from the byte buffer by using it's index of start of message.
fn read_message(buffer: &Vec<u8>, msg_start: usize) -> Message {
    let mut msg = Message::new();
    msg.start_in_buf = msg_start;
    let header = read_header(buffer[msg_start..msg_start+HEADER_SIZE].try_into().unwrap());
    msg.header = header;
    msg.payload = buffer[msg_start+HEADER_SIZE..msg_start+ msg.header.msg_size as usize].into();
    msg
}


/// Trace the contents of a message
///
/// This function simply logs a message and it's payload.
fn trace_message(msg: &Message, context:&str){
    trace!(object_id=msg.header.object_id, msg_size=msg.header.msg_size, opcode=msg.header.opcode, start_byte=msg.start_in_buf,
        payload=format!("{:?}", msg.payload), payload_str=try_read_payload_as_str(&msg.payload),
        "{}", context);
}

/// Trace a set of messages
///
/// Trace all messages present in the Vector
fn trace_messages_vec(msgs: &Vec<Message>, context:&str){
    for msg in msgs{
        trace_message(&msg, context);
    }
}

/// Reads all messages contained in the buffer
///
/// Reads and returns all messages found in the passed buffer, stops at the given buf_size argument.
/// Returns a Vector of messages.
fn read_all_messages_in_buf(buffer: &Vec<u8>, buf_size: usize) -> Vec<Message> {
    let mut msgs : Vec<Message> = vec![];
    let mut next_msg_start = 0;
    while next_msg_start < buf_size && buf_size >=HEADER_SIZE {
        let msg = read_message(&buffer, next_msg_start);
        //print_message(&msg);
        next_msg_start = get_next_msg_start(next_msg_start, &msg.header);
        //dbg!(&next_msg_start);
        msgs.push(msg);
    };
    return msgs
}

/// Tries to convert the given payload to UTF-8
///
/// Tries to convert the payload to an UTF-8 string. Returns an Option for a &str.
/// This function is commonly used for searches for certain interfaces, or a custom zone.
fn try_read_payload_as_str(payload: &Vec<u8>) -> Option<&str>{
    if let Ok(text) = std::str::from_utf8(payload){
        return Some(text);
    } else {
        None
    }
}

/// Searches for a certain message in the passed Vector
///
/// Searches for a message containing the passed Object_ID, Opcode and payload text in the passed Vector of messages.
/// Disregards the Object_ID argument if it is set to `0`. Disregards text to search in the payload if it is set to `""`.
/// The payload text passed in the arguments does not need to be the exact same as the one in the message, because this
/// function uses the 'contains' method.
fn search_for_msg(msgs: &Vec<Message>, obj_id: u32, opcode: u16, text: &str) -> Option<usize> {
    for msg in msgs {
        if (obj_id == 0) || (msg.header.object_id == obj_id) {
            if msg.header.opcode == opcode {
                if text == "" {
                    return Some(msg.start_in_buf);
                }
                if let Some(text_payload) = try_read_payload_as_str(&msg.payload){
                    //println!("search text_payload={text_payload}");
                    if text_payload.contains(text){
                        return Some(msg.start_in_buf)
                    };
                }
            };
        }
    };
    None
}

/// Grabs the last Object_ID in a message
///
/// Grabs and returns the last Object_ID in the message of the buffer described by its message position
/// (argument `msg_pos`). Some Wayland messages have a Wayland seat argument as their last parameter, so
/// the presence of such a seat must be passed to this function. Uses the `get_last_id_in_msg` function.
fn get_last_id_in_msg_via_buffer(buffer: &Vec<u8>, msg_pos: usize, has_wl_seat: bool) -> u32 {
    get_last_id_in_msg(&read_message(buffer, msg_pos), has_wl_seat)
}

/// Grabs the last Object_ID in the passed message
///
/// Grabs and returns the last Object_ID in the passed message.
/// Some Wayland messages have a Wayland seat argument as their last parameter, so the presence of such a
/// seat must be passed to this function. Uses the `get_last_id_in_msg` function.
fn get_last_id_in_msg(msg: &Message, has_wl_seat: bool) -> u32 {
    let payload_length: usize = (msg.header.msg_size as usize) - HEADER_SIZE;
    if has_wl_seat {
        u32::from_le_bytes(msg.payload[payload_length-2*WORD_SIZE..payload_length-WORD_SIZE].try_into().unwrap())
    } else {
        u32::from_le_bytes(msg.payload[payload_length-WORD_SIZE..].try_into().unwrap())
    }
}

/// Extracts messages by filtering by O_IDs in id_if
///
/// Extracts messages from the passed Vector by filtering by Object_IDs present in the
/// objectIDs_to_InterFaces (argument `id_if`) data structure. Returns a new Vector of messages.
fn extract_msgs_by_id_if (msgs: &Vec<Message>, id_if: &Vec<(&str, u32)>) -> Vec<Message> {
    let mut msgs_filtered: Vec<Message> = vec![];
    for msg in msgs {
        for duo in id_if {
            if msg.header.object_id == duo.1 {
                msgs_filtered.push(msg.clone());
            }
        }
    };
    msgs_filtered
}

/// Searches for a message and gets it's last O_ID
///
/// Searches for a message in the passed buffer by using passed Object_ID, Opcode, and payload text. Must specify if message
/// contains a Wayland Seat argument. Returns an Option of a u32.
fn search_and_try_get_id(buf: &Vec<u8>, msgs: &Vec<Message>, obj_id: u32, opcode: u16, text: &str, has_wl_seat: bool) -> Option<u32> {
    if let Some(obj_pos) = search_for_msg(msgs, obj_id, opcode, text){
        let obj_id = get_last_id_in_msg_via_buffer(buf, obj_pos, has_wl_seat);
        Some(obj_id)
    } else {
        None
    }
}

/// Deletes an ID in the id_if argument
///
/// Deletes the passed Object_ID from the objectIDs_to_InterFaces (argument `id_if`) data structure.
/// This function is used when the deletion of a Wayland object is detected.
fn delete_id(id_if: &mut Vec<(&str, u32)>, id_to_del: u32){
    id_if.retain( |&elt| elt.1 != id_to_del); // closure activates removal of elt in id_if when elt obj_id == id_to_del.
}


/// Gets the O_ID via an object name.
///
/// Gets the Object_ID present in the objectIDs_to_InterFaces (argument `id_if`) by using
/// the object's name, or more precisely, the name of the object's interface. Returns 0 if
/// the Object_ID is not found.
fn get_oid_via_name(id_if: &Vec<(&str, u32)>, obj_name: &str) -> u32 {
    for elt in id_if {
        if elt.0 == obj_name{
            return elt.1
        }
    };
    0
}


/// Gets the messages to block
///
/// Gets and Returns a Vector of the messages to block. The objectIDs_to_InterFaces (argument `id_if`) data structure
/// is used to link the objects interfaces names in the Elements_to_Block (argument `elts_to_block`) data structure
/// with the Object_IDs in the original Vector of messages. Returns a new Vector.
fn get_msgs_to_block(msgs: &Vec<Message>, id_if: &Vec<(&str, u32)>, elts_to_block: &Vec<(&str, u16)>) -> Vec<Message> {
    let mut msgs_to_block: Vec<Message> = vec![];
    let mut duos_to_block: Vec<(u32, u16)> = vec![];
    // Create duos of ObjectIDs and Opcodes to block
    for elt in elts_to_block {
        for listing in id_if {
            if listing.0 == elt.0 {
                duos_to_block.push((listing.1, elt.1))
            }
        }
    }
    // Get messages that match these Duos
    for msg in msgs {
        for duo in duos_to_block.clone() {
            if (msg.header.object_id == duo.0) && (msg.header.opcode == duo.1) {
                msgs_to_block.push(msg.clone());
            }
        }
    }
    msgs_to_block
}


/// Checks for deleted objects
///
/// Checks for deleted Wayland objects in the passed Vector of messages, and deletes
/// them from the objectIDs_to_InterFaces (argument `id_if`) data structure if some are found.
fn check_for_deleted_obj(msgs_filtered: &Vec<Message>, id_if: &mut Vec<(&str, u32)>){
    for msg in msgs_filtered {
        for elt in id_if.clone() {
            if msg.header.object_id == elt.1 {
                if elt.0 == "wl_data_device" && msg.header.opcode == DD_R_DESTROY {
                    delete_id(id_if, elt.1);
                    trace!("obj data_device (id={:?}) deleted.", elt.1);
                }
                if elt.0 == "wl_data_source" && msg.header.opcode == DS_R_DESTROY {
                    delete_id(id_if, elt.1);
                    trace!("obj data_source (id={:?}) deleted.", elt.1);
                }
                if elt.0 == "wl_data_offer" && msg.header.opcode == DO_R_DESTROY {
                    delete_id(id_if, elt.1);
                    trace!("obj data_offer (id={:?}) deleted.", elt.1);
                }
                if elt.0 == "zwp_primary_selection_device" && msg.header.opcode == PS_D_R_DESTROY {
                    delete_id(id_if, elt.1);
                    trace!("obj primary_selection_device (id={:?}) deleted.", elt.1);
                }
                if elt.0 == "zwp_primary_selection_offer" && msg.header.opcode == PS_O_R_DESTROY {
                    delete_id(id_if, elt.1);
                    trace!("obj primary_selection_offer (id={:?}) deleted.", elt.1);
                }
                if elt.0 == "zwp_primary_selection_device_manager" && msg.header.opcode == PS_DM_R_DESTROY {
                    delete_id(id_if, elt.1);
                    trace!("obj primary_selection_device_manager (id={:?}) deleted.", elt.1);
                }
            }
        }
    }
}


/// Lists the indexes (in reverse) to be deleted
///
/// Lists in reverse the indexes that need to be deleted from the buffer.
/// These indexes are created by knowing which messages need to be blocked
/// (as they are passed as an argument of the fuction).
/// This list is made in reverse so that when the bytes are deleted one by
/// one (biggest to smallest), each index still corresponds to it's byte.
fn get_reverse_indexes_to_delete(msgs_to_block: &Vec<Message>) -> Vec<usize> {
    let mut indexes: Vec<usize> = vec![];
    for msg in msgs_to_block {
        let mut i: usize = msg.start_in_buf;
        // println!("msg.start_in_buf in_func in_loop: {:?}", msg.start_in_buf);
        while i < (msg.start_in_buf+(msg.header.msg_size as usize)) {
            indexes.push(i);
            i = i+1;
        }
    }
    // println!("indexes in_func after insert before reverse: {:?}", indexes);
    indexes.reverse();
    indexes
}


/// Deletes the passed indexes from the buffer
///
/// Deletes the passed indexes (that need to be in reverse) from the buffer.
/// This fuction modifies the buffer and returns the number of bytes that have been deleted.
fn delete_indexes_from_buf(buf: &mut Vec<u8>, indexes: &Vec<usize>) -> usize {
    let mut count: usize = 0;
    if !indexes.is_empty() {
        if indexes[0] < buf.len() {
            for index in indexes {
                buf.remove(*index);
                count = count+1;
            }
        }
    }
    count
}


/// Try to get Custom Zone
///
/// Try to get Custom Zone in the passed Vector of messages and buffer. Needs the wl_data_offer Object_ID.
/// Returns an Option of a string (with the zone name).
fn try_get_custom_zone_in_msgs(msgs: &Vec<Message>, buf: &Vec<u8>, do_id: u32) -> Option<String> {
    if let Some(zone_msg_pos) = search_for_msg(msgs, do_id, DO_E_O, MIME_TYPE_PREFIX){
        let zone_msg: Message = read_message(buf, zone_msg_pos);
        if let Some(str_payload) = try_read_payload_as_str(&zone_msg.payload) {
            // Split payload str to get zone number
            dbg!(&str_payload);
            let (_, data) = str_payload.split_at(4+MIME_TYPE_PREFIX.len()); // 4 is the string's length
            let idx_split = data.find('\0').unwrap_or(data.len());
            let (zone_str, _) = data.split_at(idx_split);
            dbg!(&data);
            if zone_str.is_empty() {
                return None
            }
            return Some(zone_str.to_string());
        }
    }
    None
}


/// Checks if a certain interface is in the id_if
///
/// Checks if a certain interface name is in the objectIDs_to_InterFaces (argument `id_if`) data structure.
fn object_found_in_id_if(id_if: &Vec<(&str, u32)>, interf: &str) -> bool {
    for duo in id_if {
        if duo.0 == interf {
            return true
        }
    }
    false
}

const fn is_target_little_endian() -> bool {
    u16::from_ne_bytes([1, 0]) == 1
}

/// Creates the Custom Zone message
///
/// Creates the Custom Zone message by using the passed Custom Zone name (argument
/// `this_zone`) and the wl_data_source Object_ID. Returns a tuple of a buffer
/// containing the new message, and the size of this buffer.
fn create_zone_msg(ds_id: u32, this_zone: String) -> (Vec<u8>, u16) {
    // Refer to Wayland's wire format: https://wayland.freedesktop.org/docs/book/Protocol.html#wire-format
    // a string in Wayland wire format: Starts with an unsigned 32-bit length (including null terminator),
    // followed by the UTF-8 encoded string contents, including terminating null byte, then padding to a 32-bit boundary.
    // A null value is represented with a length of 0. Interior null bytes are not permitted.
    let mut new_msg: Vec<u8> = vec![0; 12];
    let new_id_bytes: Vec<u8> = u32::to_ne_bytes(ds_id).to_vec();
    new_msg.splice(0..4, new_id_bytes);
    let opcode_bytes: Vec<u8> = u16::to_ne_bytes(DS_R_OFFER).to_vec();

    if is_target_little_endian() {
        new_msg.splice(4..6, opcode_bytes);
    }
    else {
        new_msg.splice(6..8, opcode_bytes);
    }

    let mut data: Vec<u8>=MIME_TYPE_PREFIX.as_bytes().into();
    data.append(&mut this_zone.as_bytes().to_vec());
    data.push(0);
    let mut data_len=data.len();
    for _ in 0..(4-data_len%4) {
        data.push(0);
        data_len+=1
    }
    new_msg.append(&mut data); // modifies the data variable!

    let string_size_bytes: Vec<u8> = u32::to_ne_bytes(data_len as u32).to_vec();
    new_msg.splice(8..12, string_size_bytes);

    let msg_size=new_msg.len() as u16;
    let new_size_bytes: Vec<u8> = u16::to_ne_bytes(msg_size).to_vec();
    if is_target_little_endian() {
        new_msg.splice(6..8, new_size_bytes);
    }
    else {
        new_msg.splice(4..6, new_size_bytes);
    }
    (new_msg, msg_size)
}


/// Inserts the Custom Zone message in the buffer
///
/// Inserts the Custom Zone message created by using the `create_zone_msg` function into the passed buffer.
/// Needs the wl_data_offer position (in buffer) to be able to insert the message correctly.
/// Returns a tuple of the modified buffer and the size of the newly inserted message.
fn insert_zone_msg_in_buf(mut client_buf: Vec<u8>, ds_offer_pos: usize, ds_id: u32, this_zone: String) -> (Vec<u8>, u16) {
    let (new_zone_msg, new_msg_size) = create_zone_msg(ds_id, this_zone);

    // Insert custom_zone_msg right before a DS_offer msg.
    let ds_offer_pos: usize = ds_offer_pos;

    // insert custom_zone_msg into client_buffer
    client_buf.splice(ds_offer_pos..ds_offer_pos, new_zone_msg);

    let inserted_msg: Message = read_message(&client_buf, ds_offer_pos);
    trace_message(&inserted_msg, "inserted zone message in buffer");
    (client_buf, new_msg_size)
}


/// Gets the launch arguments of the proxy
///
/// Gets the arguments specified at the proxy launch.
/// The order for the return tuple of these arguments are :
/// real_compo_path, fake_compo_path, present_zone_name, authorized_zones, block_bool
fn get_args() -> (String, String, String, String, bool) {
    // Get arguments and initialize values from them

    let args:Vec<String> = env::args().collect();
    let server_path=if args.len()>1 {
        args[1].clone()
    }
    else {
        String::from("/tmp/mock-server.sock")
    };

    let proxy_service_path=if args.len()>=3 {
        args[2].clone()
    }
    else {
        String::from("/tmp/proxy.sock")
    };

    let this_zone_arg: String = if args.len()>=4 {
        args[3].clone()
    } else {
        String::from("1")
    };

    let authorized_zones_arg: String = if args.len()>=5 {
        args[4].clone()
    } else {
        String::from("")
    };

    let block_bool=if args.len()==6 {
        if args[5] == "allow" {
            false
        } else {
            true
        }
    } else {
        true
    };
    return (server_path, proxy_service_path, this_zone_arg, authorized_zones_arg, block_bool);
}


/// Gets the current time since epoch
///
/// Gets the current time since epoch. Used for logging purposes.
fn _get_time_since_epoch() -> String {
    let now: SystemTime = SystemTime::now();
    let since_the_epoch = now.duration_since(UNIX_EPOCH).expect("Time should go forward.");
    let res: String = format!("{:?}",since_the_epoch);
    res
}


/// Runs the proxy
///
/// This is the primary (and only) function to run the proxy. Initialises an infinite loop.
#[tokio::main]
async fn main() -> anyhow::Result<()> {
    // init logging
    let mut log_dir=String::from("/var/log");
    if let Ok(v)=env::var("LOG_DIR") {
        log_dir=String::from(v)
    }
    println!("Logging to directory '{}'", log_dir);
    let trace_conf= TraceConfig::new(&log_dir, "wayland-proxy")
        .with_stdout_output(false)
        .with_file_level(LevelFilter::INFO)
        .with_syslog_level(LevelFilter::WARN);
    let _t=tracing_setup_json(&trace_conf).expect("Failed to initialize logging");

    // Initialize args
    let (server_path, proxy_service_path, this_zone_arg, authorized_zones_arg, block_bool) = get_args();

    info!("Starting Wayland proxy for zone '{}', can paste data copied from {}", this_zone_arg, authorized_zones_arg);
    info!("Listening on {proxy_service_path}");
    info!("Actual server at {server_path}");

    // Remove any existing socket file
    if Path::new(&proxy_service_path).exists() {
        std::fs::remove_file(&proxy_service_path)?;
    }
    let listener = UnixListener::bind(&proxy_service_path)?;


    // Main client connection loop
    loop {
        let (client_stream_nofd, _) = listener.accept()?;
        info!("Client connected");

        // Redefine args because of lifetime problems
        let server_path = server_path.to_string();
        let authorized_zones_arg = authorized_zones_arg.to_string();
        let this_zone_arg = this_zone_arg.to_string();

        tokio::spawn(async move {
            let server_stream_nofd=match StdUnixStream::connect(&server_path) {
                Ok(s) => s,
                Err(e) => {
                    error!("Failed to connect to server: {}", e);
                    return
                }
            };
            let mut client_buf = vec![0u8; BUFSIZE];
            let mut server_buf = vec![0u8; BUFSIZE];

            // Transform UnixStream into UnixFdStream (wrapper of UnixStream from asyncfd crate)
            let mut client_stream = UnixFdStream::new(client_stream_nofd, 4).unwrap();
            let mut server_stream = UnixFdStream::new(server_stream_nofd, 4).unwrap();


            // Variables for msg deserialisation (parsing) and object identification/tracking
            let mut id_if: Vec<(&str, u32)> = vec![];      // objectIDs_to_InterFaces


            // Set list of interfaces and opcodes to block if previously defined argument of CLI asks for it.
            // Elements_to_Block
            let mut elts_to_block: Vec<(&str, u16)> = vec![];
            if block_bool {
                elts_to_block = vec![
                    ("wl_data_offer", DO_E_O),
                    //("wl_data_device", DD_E_SEL),
                    ("zwp_primary_selection_offer", PS_O_E_O),
                    //("zwp_primary_selection_device", PS_D_E_SEL),
                ];
            }

            // Initialise variable authorized_zones from previous argument of CLI, and add current zone to it.
            let mut authorized_zones: HashSet<&str> = HashSet::new();
            if !authorized_zones_arg.is_empty() {
                authorized_zones = authorized_zones_arg.split(',').collect();
            }
            authorized_zones.insert(&this_zone_arg);
            info!("can copy from zones: {:?}", &authorized_zones);

            loop {

                tokio::select! {
                    result=client_stream.read(&mut client_buf) => {
                        match result {
                            Ok(n) if n>0 => {
                                // Check for FDs
                                forward_all_fd(&mut client_stream, &mut server_stream).await;

                                let msgs = read_all_messages_in_buf(&client_buf[..n].into(), n);
                                let mut duplicated_msg_size: u16 = 0;

                                if !object_found_in_id_if(&id_if, "wl_data_device_manager") || !object_found_in_id_if(&id_if, "wl_data_device") {
                                    trace_messages_vec(& msgs, "from client, !wl_data_device*");

                                    // Search for DDM
                                    if let Some(ddm_id) = search_and_try_get_id(&client_buf, &msgs, 0, 0, "wl_data_device_manager", false) {
                                        id_if.push(("wl_data_device_manager",ddm_id));
                                        // If DDM found, also search for PS_DM
                                        if let Some(ps_dm_id) = search_and_try_get_id(&client_buf, &msgs, 0, 0, "zwp_primary_selection_device_manager", false) {
                                            trace!("PS_DM_id={:?}",ps_dm_id);
                                            id_if.push(("zwp_primary_selection_device_manager",ps_dm_id));
                                            // If PS_DM found, also search for PS_D
                                            if let Some(ps_d_id) = search_and_try_get_id(&client_buf, &msgs, ps_dm_id, PS_DM_R_GET_PS_D, "", true) {
                                                trace!("PS_D_ID={:?}",ps_d_id);
                                                id_if.push(("zwp_primary_selection_device",ps_d_id));
                                            }
                                        }
                                        // If DDM found, also search for DD
                                        if let Some(dd_id) = search_and_try_get_id(&client_buf, &msgs, ddm_id, DDM_R_GET_DD, "", true){
                                            id_if.push(("wl_data_device",dd_id));
                                        }
                                    }
                                } else {
                                    // Search for DS
                                    let ddm_id = get_oid_via_name(&id_if, "wl_data_device_manager");
                                    if let Some(ds_id) = search_and_try_get_id(&client_buf, &msgs, ddm_id, DDM_R_CREATE_DS, "", false){
                                        id_if.push(("wl_data_source",ds_id));
                                        // Find a DS_offer msg to copy and modify for custom MIME type
                                        if let Some(ds_offer_pos) = search_for_msg(&msgs, ds_id, DS_R_OFFER, "") {
                                            (client_buf, duplicated_msg_size) = insert_zone_msg_in_buf(
                                                client_buf,
                                                ds_offer_pos,
                                                ds_id,
                                                this_zone_arg.clone()
                                            );
                                            debug!("Inserted custom zone msg");
                                        }
                                    }
                                    let msgs_filtered: Vec<Message> = extract_msgs_by_id_if(&msgs, &id_if);
                                    if !msgs_filtered.is_empty() {
                                        check_for_deleted_obj(&msgs_filtered, &mut id_if);
                                        trace_messages_vec(&msgs_filtered, "from client, filtered");
                                    }
                                }

                                if let Err(e) = server_stream.write_all(&client_buf[..n+duplicated_msg_size as usize]).await {
                                    error!("Failed to forward message to server: {}", e);
                                    break
                                }
                            },
                            Ok(_) => {
                                info!("Client disconnected");
                                return;
                            }
                            Err(e) => {
                                error!("Error reading from client: {e}");
                                return;
                            }
                        }
                    }
                    result=server_stream.read(&mut server_buf) => {
                        match result {
                            Ok(n) if n>0 => {
                                // Check for FDs
                                forward_all_fd(&mut server_stream, &mut client_stream).await;

                                let msgs = read_all_messages_in_buf(&server_buf[..n].into(), n);

                                let mut new_n = n;
                                let mut zone_says_block: bool = true;

                                if !object_found_in_id_if(&id_if, "wl_data_device_manager") {
                                    trace_messages_vec(& msgs, "from_server, !wl_data_device_manager");
                                } else {
                                    // Search for DO
                                    let dd_id = get_oid_via_name(&id_if, "wl_data_device");
                                    if let Some(do_id) = search_and_try_get_id(&server_buf, &msgs, dd_id, DD_E_DO, "", false){
                                        trace!("DO_ID={:?}",do_id);
                                        id_if.push(("wl_data_offer",do_id));
                                        // If DO found, try and search for custom zone inside events from DO (wl_data_offer.offer)
                                        if let Some(zone) = try_get_custom_zone_in_msgs(&msgs, &server_buf, do_id) {
                                            trace!("ZONE FOUND, zone={:?}", zone);
                                            if authorized_zones.contains(&*zone) {        // "&*zone" is used to convert zone from String to &str
                                                zone_says_block = false;
                                            }
                                            trace!("Zone={:?} found in message from Compositor. Unblocking copy-paste.", zone);
                                        } else {
                                            trace!("Searched for custom ZONE, but none found.");
                                        }
                                    }
                                    // Search for PS_O
                                    let ps_d_id = get_oid_via_name(&id_if, "zwp_primary_selection_device");
                                    if let Some(ps_o_id) = search_and_try_get_id(&server_buf, &msgs, ps_d_id, PS_D_E_DO, "", false) {
                                        trace!("PS_O_ID={:?}",ps_o_id);
                                        id_if.push(("zwp_primary_selection_offer",ps_o_id));
                                    }

                                    let msgs_filtered: Vec<Message> = extract_msgs_by_id_if(&msgs, &id_if);
                                    if !msgs_filtered.is_empty() {
                                        trace_messages_vec(&msgs_filtered, "from server, filtered, before");

                                        // loop for smart blocking
                                        if !elts_to_block.is_empty() && zone_says_block {
                                            let msgs_to_block = get_msgs_to_block(&msgs_filtered, &id_if, &elts_to_block);

                                            // Get indexes_to_delete and delete them
                                            let indexes = get_reverse_indexes_to_delete(&msgs_to_block);
                                            trace!("Indexes_to_delete : {:?}", indexes);
                                            let deleted_size: usize = delete_indexes_from_buf(&mut server_buf, &indexes);
                                            dbg!(&n);
                                            dbg!(&deleted_size);
                                            let buf_size_after_del = n-deleted_size;
                                            dbg!(&buf_size_after_del);

                                            // Print filtered messages that were not deleted.
                                            let msgs = read_all_messages_in_buf(&server_buf[..n].to_vec(), buf_size_after_del);
                                            let msgs_filtered: Vec<Message> = extract_msgs_by_id_if(&msgs, &id_if);
                                            trace_messages_vec(&msgs_filtered, "from server, filtered, after");

                                            new_n = buf_size_after_del;
                                        }
                                    }
                                }
                                if let Err(e) = client_stream.write_all(&server_buf[..new_n]).await {
                                    error!("Failed to forward message to client: {}", e);
                                    break
                                }
                            },
                            Ok(_) => {
                                info!("Server disconnected");
                                return
                            }
                            Err(e) => {
                                error!("Error reading from server: {}", e);
                                return
                            }
                        }
                    }
                }
            }
            info!("Stopped proxy");
        });
    }
}
