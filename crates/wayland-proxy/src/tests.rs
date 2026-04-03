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

/// Test module composed of unitary tests for each functions.
/// Launch with `cargo test`.

use super::*;


#[test]
fn test_read_header_1() {
    let test_bytes_header: Vec<u8> = vec![2, 0, 0, 0, 0, 0, 24, 0, 1];
    let correct_header: MsgHeader = MsgHeader { object_id: (2), msg_size: (24), opcode: (0) };
    assert_eq!(read_header(test_bytes_header[0..8].try_into().unwrap()), correct_header);
}

#[test]
fn test_get_next_msg_start_1() {
    let header: MsgHeader = MsgHeader { object_id: (2), msg_size: (24), opcode: (0) };
    assert_eq!(get_next_msg_start(0, &header), 24);
}


#[test]
fn test_read_message_1() {
    let test_bytes_msgs: Vec<u8> = vec![2, 0, 0, 0, 0, 0, 36, 0, 1, 0, 0, 0, 14, 0, 0, 0, 119, 108, 95, 99, 111, 109,
        112, 111, 115, 105, 116, 111, 114, 0, 0, 0, 6, 0, 0, 0, 2, 0, 0, 0, 0, 0, 28, 0, 2, 0, 0, 0, 7, 0, 0, 0, 119,
        108, 95, 100, 114, 109, 0, 0, 2, 0, 0, 0, 2, 0, 0, 0, 0, 0, 28, 0, 3, 0, 0, 0, 7, 0, 0, 0, 119, 108, 95, 115,
        104, 109, 0, 0, 2, 0, 0, 0,];
    let correct_header: MsgHeader = MsgHeader { object_id: (2), msg_size: (36), opcode: (0) };
    let correct_msg: Message = Message {
        header: (correct_header),
        payload: (vec![1, 0, 0, 0, 14, 0, 0, 0, 119, 108, 95, 99, 111, 109, 112, 111, 115, 105, 116, 111, 114, 0, 0,
            0, 6, 0, 0, 0]),
        start_in_buf: (0)
    };
    assert_eq!(read_message(&test_bytes_msgs[0..92].to_vec(), 0), correct_msg);
}

#[test]
fn test_read_all_messages_in_buf_1() {
    let test_bytes_msgs: Vec<u8> = vec![2, 0, 0, 0, 0, 0, 36, 0, 1, 0, 0, 0, 14, 0, 0, 0, 119, 108, 95, 99, 111, 109,
        112, 111, 115, 105, 116, 111, 114, 0, 0, 0, 6, 0, 0, 0, 2, 0, 0, 0, 0, 0, 28, 0, 2, 0, 0, 0, 7, 0, 0, 0, 119,
        108, 95, 100, 114, 109, 0, 0, 2, 0, 0, 0, 2, 0, 0, 0, 0, 0, 28, 0, 3, 0, 0, 0, 7, 0, 0, 0, 119, 108, 95, 115,
        104, 109, 0, 0, 2, 0, 0, 0];
    let correct_header_1: MsgHeader = MsgHeader { object_id: (2), msg_size: (36), opcode: (0) };
    let correct_msg_1: Message = Message {
        header: (correct_header_1),
        payload: (vec![1, 0, 0, 0, 14, 0, 0, 0, 119, 108, 95, 99, 111, 109, 112, 111, 115, 105, 116, 111, 114, 0, 0,
            0, 6, 0, 0, 0]),
        start_in_buf: (0)
    };
    let correct_header_2: MsgHeader = MsgHeader { object_id: (2), msg_size: (28), opcode: (0) };
    let correct_msg_2: Message = Message {
        header: (correct_header_2),
        payload: (vec![2, 0, 0, 0, 7, 0, 0, 0, 119, 108, 95, 100, 114, 109, 0, 0, 2, 0, 0, 0]),
        start_in_buf: (36)
    };
    let correct_header_3: MsgHeader = MsgHeader { object_id: (2), msg_size: (28), opcode: (0) };
    let correct_msg_3: Message = Message {
        header: (correct_header_3),
        payload: (vec![3, 0, 0, 0, 7, 0, 0, 0, 119, 108, 95, 115, 104, 109, 0, 0, 2, 0, 0, 0]),
        start_in_buf: (64)
    };
    assert_eq!(read_all_messages_in_buf(&test_bytes_msgs, 92), vec![correct_msg_1, correct_msg_2, correct_msg_3]);
}


#[test]
fn test_try_read_payload_as_str_1() {
    let correct_header: MsgHeader = MsgHeader { object_id: (2), msg_size: (36), opcode: (0) };
    let correct_msg: Message = Message {
        header: (correct_header),
        payload: (vec![1, 0, 0, 0, 14, 0, 0, 0, 119, 108, 95, 99, 111, 109, 112, 111, 115, 105, 116, 111, 114, 0, 0,
            0, 6, 0, 0, 0]),
        start_in_buf: (0)
    };
    assert_eq!(try_read_payload_as_str(&correct_msg.payload), Some("\u{1}\0\0\0\u{e}\0\0\0wl_compositor\0\0\0\u{6}\0\0\0"));
}

#[test]
fn test_read_all_messages_in_buf_2() {
    let test_bytes_msgs: Vec<u8> = vec![1, 0, 0, 0, 1, 0, 12, 0, 2, 0, 0, 0, 1, 0, 0, 0, 0, 0, 12, 0, 3, 0, 0, 0];
    let correct_header_1: MsgHeader = MsgHeader { object_id: (1), msg_size: (12), opcode: (1) };
    let correct_msg_1: Message = Message {
        header: (correct_header_1),
        payload: (vec![2, 0, 0, 0]),
        start_in_buf: (0)
    };
    let correct_header_2: MsgHeader = MsgHeader { object_id: (1), msg_size: (12), opcode: (0) };
    let correct_msg_2: Message = Message {
        header: (correct_header_2),
        payload: (vec![3, 0, 0, 0]),
        start_in_buf: (12)
    };
    assert_eq!(read_all_messages_in_buf(&test_bytes_msgs, 24), vec![correct_msg_1, correct_msg_2]);
}

#[test]
fn test_try_read_payload_as_str_2() {
    let correct_header_1: MsgHeader = MsgHeader { object_id: (1), msg_size: (12), opcode: (1) };
    let correct_msg_1: Message = Message {
        header: (correct_header_1),
        payload: (vec![2, 0, 0, 0]),
        start_in_buf: (0)
    };
    assert_eq!(try_read_payload_as_str(&correct_msg_1.payload), Some("\u{2}\0\0\0"));
}


#[test]
fn test_search_for_msg_1() {
    let correct_header_1: MsgHeader = MsgHeader { object_id: (2), msg_size: (36), opcode: (0) };
    let correct_msg_1: Message = Message {
        header: (correct_header_1),
        payload: (vec![1, 0, 0, 0, 14, 0, 0, 0, 119, 108, 95, 99, 111, 109, 112, 111, 115, 105, 116, 111, 114, 0, 0,
            0, 6, 0, 0, 0]),
        start_in_buf: (0)
    };
    let correct_header_2: MsgHeader = MsgHeader { object_id: (2), msg_size: (28), opcode: (0) };
    let correct_msg_2: Message = Message {
        header: (correct_header_2),
        payload: (vec![2, 0, 0, 0, 7, 0, 0, 0, 119, 108, 95, 100, 114, 109, 0, 0, 2, 0, 0, 0]),
        start_in_buf: (36)
    };
    let correct_header_3: MsgHeader = MsgHeader { object_id: (1), msg_size: (12), opcode: (1) };
    let correct_msg_3: Message = Message {
        header: (correct_header_3),
        payload: (vec![2, 0, 0, 0]),
        start_in_buf: (64)
    };
    assert_eq!(search_for_msg(&vec![correct_msg_1.clone(), correct_msg_2.clone(), correct_msg_3.clone()], 2, 0, "compo"), Some(0));
    assert_eq!(search_for_msg(&vec![correct_msg_1.clone(), correct_msg_2.clone(), correct_msg_3.clone()], 2, 0, ""), Some(0));
    assert_eq!(search_for_msg(&vec![correct_msg_1.clone(), correct_msg_2.clone(), correct_msg_3.clone()], 0, 0, "Abracadabra"), None);
    assert_eq!(search_for_msg(&vec![correct_msg_1.clone(), correct_msg_2.clone(), correct_msg_3.clone()], 0, 1, ""), Some(64));
    assert_eq!(search_for_msg(&vec![correct_msg_1.clone(), correct_msg_2.clone(), correct_msg_3.clone()], 0, 0, "drm"), Some(36));
    assert_eq!(search_for_msg(&vec![correct_msg_1, correct_msg_2, correct_msg_3], 23, 0, ""), None);
}


#[test]
fn test_get_last_id_in_msg_1() {
    let correct_header_1: MsgHeader = MsgHeader { object_id: (2), msg_size: (36), opcode: (0) };
    let correct_msg_1: Message = Message {
        header: (correct_header_1),
        payload: (vec![1, 0, 0, 0, 14, 0, 0, 0, 119, 108, 95, 99, 111, 109, 112, 111, 115, 105, 116, 111, 114, 0, 0,
            0, 6, 0, 0, 0]),
        start_in_buf: (0)
    };
    let correct_header_2: MsgHeader = MsgHeader { object_id: (1), msg_size: (12), opcode: (1) };
    let correct_msg_2: Message = Message {
        header: (correct_header_2),
        payload: (vec![2, 0, 0, 0]),
        start_in_buf: (0)
    };
    let correct_header_3: MsgHeader = MsgHeader { object_id: (2), msg_size: (28), opcode: (0) };
    let correct_msg_3: Message = Message {
        header: (correct_header_3),
        payload: (vec![3, 0, 0, 0, 7, 0, 0, 0, 119, 108, 95, 115, 104, 0, 0, 0, 84, 0, 0, 0]),
        start_in_buf: (64)
    };
    assert_eq!(get_last_id_in_msg(&correct_msg_1.clone(), false), 6 as u32);
    assert_eq!(get_last_id_in_msg(&correct_msg_2.clone(), false), 2 as u32);
    assert_eq!(get_last_id_in_msg(&correct_msg_3.clone(), false), 84 as u32);
    assert_eq!(get_last_id_in_msg(&correct_msg_3.clone(), true), 104 as u32);
}


#[test]
fn test_get_last_id_in_msg_via_buffer_1() {
    let test_bytes_msgs: Vec<u8> = vec![2, 0, 0, 0, 0, 0, 36, 0, 1, 0, 0, 0, 14, 0, 0, 0, 119, 108, 95, 99, 111, 109,
        112, 111, 115, 105, 116, 111, 114, 0, 0, 0, 6, 0, 0, 0, 2, 0, 0, 0, 0, 0, 28, 0, 2, 0, 0, 0, 7, 0, 0, 0, 119,
        108, 95, 100, 114, 109, 0, 0, 2, 0, 0, 0, 2, 0, 0, 0, 0, 0, 28, 0, 3, 0, 0, 0, 7, 0, 0, 0, 119, 108, 95, 115,
        104, 0, 0, 0, 84, 0, 0, 0];
    assert_eq!(get_last_id_in_msg_via_buffer(&test_bytes_msgs, 36, false), 2);
    assert_eq!(get_last_id_in_msg_via_buffer(&test_bytes_msgs, 0, false), 6);
    assert_eq!(get_last_id_in_msg_via_buffer(&test_bytes_msgs, 64, false), 84);
    assert_eq!(get_last_id_in_msg_via_buffer(&test_bytes_msgs, 64, true), 104);
}


#[test]
fn test_extract_msgs_by_id_if_1() {
    let correct_header_1: MsgHeader = MsgHeader { object_id: (2), msg_size: (36), opcode: (0) };
    let correct_msg_1: Message = Message {
        header: (correct_header_1),
        payload: (vec![1, 0, 0, 0, 14, 0, 0, 0, 119, 108, 95, 99, 111, 109, 112, 111, 115, 105, 116, 111, 114, 0, 0,
            0, 6, 0, 0, 0]),
        start_in_buf: (0)
    };
    let correct_header_2: MsgHeader = MsgHeader { object_id: (1), msg_size: (12), opcode: (1) };
    let correct_msg_2: Message = Message {
        header: (correct_header_2),
        payload: (vec![2, 0, 0, 0]),
        start_in_buf: (0)
    };
    let correct_header_3: MsgHeader = MsgHeader { object_id: (2), msg_size: (28), opcode: (0) };
    let correct_msg_3: Message = Message {
        header: (correct_header_3),
        payload: (vec![3, 0, 0, 0, 7, 0, 0, 0, 119, 108, 95, 115, 104, 0, 0, 0, 84, 0, 0, 0]),
        start_in_buf: (64)
    };
    let msgs_vec: Vec<Message> = vec![correct_msg_1.clone(), correct_msg_2.clone(), correct_msg_3.clone()];
    let id_if_2: Vec<(&str, u32)> = vec![("two", 2)].into_iter().collect();
    let id_if_1: Vec<(&str, u32)> = vec![("one", 1)].into_iter().collect();
    let id_if_0: Vec<(&str, u32)> = vec![].into_iter().collect();
    let id_if_12: Vec<(&str, u32)> = vec![("two", 2), ("one", 1)].into_iter().collect();
    assert_eq!(extract_msgs_by_id_if(&msgs_vec, &id_if_2), vec![correct_msg_1.clone(), correct_msg_3.clone()]);
    assert_eq!(extract_msgs_by_id_if(&msgs_vec, &id_if_1), vec![correct_msg_2.clone()]);
    assert_eq!(extract_msgs_by_id_if(&msgs_vec, &id_if_0), vec![]);
    assert_eq!(extract_msgs_by_id_if(&msgs_vec, &id_if_12), vec![correct_msg_1.clone(), correct_msg_2, correct_msg_3.clone()]);
}


#[test]
fn test_search_and_try_get_id_1() {
    let test_bytes_msgs: Vec<u8> = vec![2, 0, 0, 0, 0, 0, 36, 0, 1, 0, 0, 0, 14, 0, 0, 0, 119, 108, 95, 99, 111, 109,
        112, 111, 115, 105, 116, 111, 114, 0, 0, 0, 6, 0, 0, 0, 1, 0, 0, 0, 0, 0, 28, 0, 2, 0, 0, 0, 7, 0, 0, 0, 119,
        108, 95, 100, 114, 109, 0, 0, 2, 0, 0, 0, 4, 0, 0, 0, 0, 0, 28, 0, 3, 0, 0, 0, 7, 0, 0, 0, 119, 108, 95, 115,
        104, 0, 0, 0, 2, 0, 0, 0];
    let correct_header_1: MsgHeader = MsgHeader { object_id: (2), msg_size: (36), opcode: (0) };
    let correct_msg_1: Message = Message {
        header: (correct_header_1),
        payload: (vec![1, 0, 0, 0, 14, 0, 0, 0, 119, 108, 95, 99, 111, 109, 112, 111, 115, 105, 116, 111, 114, 0, 0,
            0, 6, 0, 0, 0]),
        start_in_buf: (0)
    };
    let correct_header_2: MsgHeader = MsgHeader { object_id: (1), msg_size: (28), opcode: (0) };
    let correct_msg_2: Message = Message {
        header: (correct_header_2),
        payload: (vec![2, 0, 0, 0, 7, 0, 0, 0, 119, 108, 95, 100, 114, 109, 0, 0, 2, 0, 0, 0]),
        start_in_buf: (36)
    };
    let correct_header_3: MsgHeader = MsgHeader { object_id: (4), msg_size: (28), opcode: (0) };
    let correct_msg_3: Message = Message {
        header: (correct_header_3),
        payload: (vec![3, 0, 0, 0, 7, 0, 0, 0, 119, 108, 95, 115, 104, 0, 0, 0, 2, 0, 0, 0]),
        start_in_buf: (64)
    };
    let msgs_vec = vec![correct_msg_1, correct_msg_2, correct_msg_3];
    let empty_vec: Vec<Message> = vec![];
    assert_eq!(search_and_try_get_id(&test_bytes_msgs, &msgs_vec, 4, 0, "sh", false), Some(2));
    assert_eq!(search_and_try_get_id(&test_bytes_msgs, &msgs_vec, 0, 0, "sh", false), Some(2));
    assert_eq!(search_and_try_get_id(&test_bytes_msgs, &msgs_vec, 4, 0, "sherpa", false), None);
    assert_eq!(search_and_try_get_id(&test_bytes_msgs, &msgs_vec, 3, 0, "", false), None);
    assert_eq!(search_and_try_get_id(&test_bytes_msgs, &msgs_vec, 0, 0, "sh", true), Some(104));
    assert_eq!(search_and_try_get_id(&test_bytes_msgs, &msgs_vec, 0, 0, "", false), Some(6));
    assert_eq!(search_and_try_get_id(&test_bytes_msgs, &msgs_vec, 0, 1, "", false), None);
    assert_eq!(search_and_try_get_id(&test_bytes_msgs, &empty_vec, 0, 0, "", false), None);
}



#[test]
fn test_delete_id_1() {
    let id_if_2: Vec<(&str, u32)> = vec![("two", 2)].into_iter().collect();
    let mut id_if_0: Vec<(&str, u32)> = vec![].into_iter().collect();
    let mut id_if_12: Vec<(&str, u32)> = vec![("two", 2), ("one", 1)].into_iter().collect();
    // Tests on id_if_12
    delete_id(&mut id_if_12, 1);
    assert_eq!(id_if_12, id_if_2);
    delete_id(&mut id_if_12, 1);
    assert_eq!(id_if_12, id_if_2);
    delete_id(&mut id_if_12, 2);
    assert_eq!(id_if_12, id_if_0);

    // Tests on id_if_0
    delete_id(&mut id_if_0, 4);
    assert_eq!(id_if_0, id_if_0);
}


#[test]
fn test_get_oid_via_name_1() {
    let id_if_0: Vec<(&str, u32)> = vec![].into_iter().collect();
    let id_if_12: Vec<(&str, u32)> = vec![("two", 2), ("one", 1)].into_iter().collect();
    assert_eq!(get_oid_via_name(&id_if_12, "one"), 1);
    assert_eq!(get_oid_via_name(&id_if_12, "two"), 2);
    assert_eq!(get_oid_via_name(&id_if_12, "four"), 0);
    assert_eq!(get_oid_via_name(&id_if_0, "four"), 0);
}

#[test]
fn test_get_msgs_to_block_1() {
    let correct_header_1: MsgHeader = MsgHeader { object_id: (2), msg_size: (36), opcode: (0) };
    let correct_msg_1: Message = Message {
        header: (correct_header_1),
        payload: (vec![1, 0, 0, 0, 14, 0, 0, 0, 119, 108, 95, 99, 111, 109, 112, 111, 115, 105, 116, 111, 114, 0, 0,
            0, 6, 0, 0, 0]),
        start_in_buf: (0)
    };
    let correct_header_2: MsgHeader = MsgHeader { object_id: (1), msg_size: (12), opcode: (1) };
    let correct_msg_2: Message = Message {
        header: (correct_header_2),
        payload: (vec![2, 0, 0, 0]),
        start_in_buf: (0)
    };
    let correct_header_3: MsgHeader = MsgHeader { object_id: (2), msg_size: (28), opcode: (2) };
    let correct_msg_3: Message = Message {
        header: (correct_header_3),
        payload: (vec![3, 0, 0, 0, 7, 0, 0, 0, 119, 108, 95, 115, 104, 0, 0, 0, 84, 0, 0, 0]),
        start_in_buf: (64)
    };
    let msgs_vec: Vec<Message> = vec![correct_msg_1.clone(), correct_msg_2.clone(), correct_msg_3.clone()];
    let id_if_12: Vec<(&str, u32)> = vec![("two", 2), ("one", 1)].into_iter().collect();
    let elt_to_block_22: Vec<(&str, u16)> = vec![("two", 2)];
    let elt_to_block_11: Vec<(&str, u16)> = vec![("one", 1)];
    let elt_to_block_20: Vec<(&str, u16)> = vec![("two", 0)];
    let elt_to_block_40: Vec<(&str, u16)> = vec![("four", 0)];
    let elt_to_block_20_22_11: Vec<(&str, u16)> = vec![("two", 0), ("two", 2), ("one", 1)];
    assert_eq!(get_msgs_to_block(&msgs_vec, &id_if_12, &elt_to_block_22), vec![correct_msg_3.clone()]);
    assert_eq!(get_msgs_to_block(&msgs_vec, &id_if_12, &elt_to_block_11), vec![correct_msg_2.clone()]);
    assert_eq!(get_msgs_to_block(&msgs_vec, &id_if_12, &elt_to_block_20), vec![correct_msg_1.clone()]);
    assert_eq!(get_msgs_to_block(&msgs_vec, &id_if_12, &elt_to_block_20_22_11), msgs_vec);
    assert_eq!(get_msgs_to_block(&msgs_vec, &id_if_12, &elt_to_block_40), vec![]);
}



#[test]
fn test_check_for_deleted_obj_1() {
    let correct_header_1: MsgHeader = MsgHeader { object_id: (2), msg_size: (36), opcode: (0) };
    let correct_msg_1: Message = Message {
        header: (correct_header_1),
        payload: (vec![1, 0, 0, 0, 14, 0, 0, 0, 119, 108, 95, 99, 111, 109, 112, 111, 115, 105, 116, 111, 114, 0, 0,
            0, 6, 0, 0, 0]),
        start_in_buf: (0)
    };
    let correct_header_2: MsgHeader = MsgHeader { object_id: (1), msg_size: (12), opcode: (DS_R_DESTROY) };
    let correct_msg_2: Message = Message {
        header: (correct_header_2),
        payload: (vec![2, 0, 0, 0]),
        start_in_buf: (0)
    };
    let correct_header_3: MsgHeader = MsgHeader { object_id: (2), msg_size: (28), opcode: (DO_R_DESTROY) };
    let correct_msg_3: Message = Message {
        header: (correct_header_3),
        payload: (vec![3, 0, 0, 0, 7, 0, 0, 0, 119, 108, 95, 115, 104, 0, 0, 0, 84, 0, 0, 0]),
        start_in_buf: (64)
    };
    let msgs_vec: Vec<Message> = vec![correct_msg_1.clone(), correct_msg_2.clone(), correct_msg_3.clone()];
    let mut id_if_2: Vec<(&str, u32)> = vec![("wl_data_source", 1)].into_iter().collect();
    let mut id_if_1: Vec<(&str, u32)> = vec![("wl_data_offer", 2)].into_iter().collect();
    let mut id_if_0: Vec<(&str, u32)> = vec![].into_iter().collect();
    let mut id_if_12: Vec<(&str, u32)> = vec![("wl_data_offer", 2), ("wl_data_source", 1)].into_iter().collect();
    check_for_deleted_obj(&msgs_vec, &mut id_if_1);
    assert_eq!(id_if_1, id_if_0);
    check_for_deleted_obj(&msgs_vec, &mut id_if_2);
    assert_eq!(id_if_2, id_if_0);
    check_for_deleted_obj(&msgs_vec, &mut id_if_12);
    assert_eq!(id_if_12, id_if_0);
    check_for_deleted_obj(&msgs_vec, &mut id_if_0);
    assert_eq!(id_if_0, vec![]);
}


#[test]
fn test_get_reverse_indexes_to_delete_1() {
    let correct_header_2: MsgHeader = MsgHeader { object_id: (1), msg_size: (12), opcode: (1) };
    let correct_msg_2: Message = Message {
        header: (correct_header_2),
        payload: (vec![2, 0, 0, 0]),
        start_in_buf: (0)
    };
    let correct_header_3: MsgHeader = MsgHeader { object_id: (2), msg_size: (28), opcode: (2) };
    let correct_msg_3: Message = Message {
        header: (correct_header_3),
        payload: (vec![3, 0, 0, 0, 7, 0, 0, 0, 119, 108, 95, 115, 104, 0, 0, 0, 84, 0, 0, 0]),
        start_in_buf: (64)
    };
    let v1:Vec<Message>=vec![];
    let v2:Vec<usize>=vec![];
    assert_eq!(get_reverse_indexes_to_delete(&v1), v2);
    assert_eq!(
        get_reverse_indexes_to_delete(&vec![correct_msg_3]),
        vec![91, 90, 89, 88, 87, 86, 85, 84, 83, 82, 81, 80, 79, 78, 77, 76, 75, 74, 73, 72, 71, 70, 69, 68, 67, 66, 65, 64]
    );
    assert_eq!(
        get_reverse_indexes_to_delete(&vec![correct_msg_2]),
        vec![11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1, 0]
    );
}


#[test]
fn test_delete_indexes_from_buf_1 (){
    let mut test_bytes: Vec<u8> = vec![0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15];
    assert_eq!(delete_indexes_from_buf(&mut test_bytes, &vec![]), 0);
    assert_eq!(test_bytes, vec![0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15]);
    assert_eq!(delete_indexes_from_buf(&mut test_bytes, &vec![3, 2, 1, 0]), 4);
    assert_eq!(test_bytes, vec![4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15]);
    assert_eq!(delete_indexes_from_buf(&mut test_bytes, &vec![22, 21]), 0);
}

#[test]
fn test_object_found_in_id_if_1 (){
    let id_if_125: Vec<(&str, u32)> = vec![("two", 2), ("one", 1), ("five", 1)].into_iter().collect();
    assert_eq!(object_found_in_id_if(&id_if_125, "one"),  true);
    assert_eq!(object_found_in_id_if(&id_if_125, "on"),  false);
    assert_eq!(object_found_in_id_if(&id_if_125, "six"),  false);
    assert_eq!(object_found_in_id_if(&vec![], "one"),  false);
    assert_eq!(object_found_in_id_if(&id_if_125, ""),  false);
}

#[test]
fn test_create_zone_msg_1 (){
    let bytes_1:Vec<u8> = vec![22, 0, 0, 0, 0, 0, 32, 0, 20, 0, 0, 0, 112, 97, 100, 115, 105, 47, 122, 111, 110, 101, 59, 122, 61, 116, 101, 115, 116, 0, 0, 0];
    let bytes_2 = vec![231, 3, 0, 0, 0, 0, 32, 0, 20, 0, 0, 0, 112, 97, 100, 115, 105, 47, 122, 111, 110, 101, 59, 122, 61, 116, 101, 115, 116, 0, 0, 0];
    let bytes_3 = vec![22, 0, 0, 0, 0, 0, 28, 0, 16, 0, 0, 0, 112, 97, 100, 115, 105, 47, 122, 111, 110, 101, 59, 122, 61, 0, 0, 0];
    assert_eq!(create_zone_msg(22, String::from("test")), (bytes_1.clone(), bytes_1.len() as u16));
    assert_eq!(create_zone_msg(999, String::from("test")), (bytes_2.clone(), bytes_2.len() as u16));
    assert_eq!(create_zone_msg(22, String::from("")), (bytes_3.clone(), bytes_3.len() as u16));
}


#[test]
fn test_try_get_custom_zone_in_msgs_1 (){
    let bytes_1 = vec![22, 0, 0, 0, 0, 0, 28, 0, 16, 0, 0, 0, 122, 45, 67, 97, 115, 115, 101, 114, 111, 108, 101, 0, 0, 0, 0, 0];
    let header_1: MsgHeader = MsgHeader { object_id: (22), msg_size: (28), opcode: (0) };
    let msg_1: Message = Message {
        header: (header_1),
        payload: (vec![16, 0, 0, 0, 122, 45, 67, 97, 115, 115, 101, 114, 111, 108, 101, 0, 0, 0, 0, 0]),
        start_in_buf: (0)
    };
    let bytes_2 = vec![16, 0, 0, 0, 0, 0, 28, 0, 16, 0, 0, 0, 122, 45, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0];
    let header_2: MsgHeader = MsgHeader { object_id: (16), msg_size: (28), opcode: (0) };
    let msg_2: Message = Message {
        header: (header_2),
        payload: (vec![16, 0, 0, 0, 0, 0, 28, 0, 16, 0, 0, 0, 122, 45, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]),
        start_in_buf: (0)
    };
    assert_eq!(try_get_custom_zone_in_msgs(&vec![msg_1], &bytes_1, 22), Some(String::from("Casserole")));
    assert_eq!(try_get_custom_zone_in_msgs(&vec![], &bytes_1, 1), None);
    assert_eq!(try_get_custom_zone_in_msgs(&vec![msg_2], &bytes_2, 16), None);
}


#[test]
fn test_insert_zone_msg_in_buf_1 (){
    let test_buf = vec![];
    let bytes_4 = vec![22, 0, 0, 0, 0, 0, 28, 0, 16, 0, 0, 0, 122, 45, 67, 97, 115, 115, 101, 114, 111, 108, 101, 0, 0, 0, 0, 0];
    let bytes_3 = vec![22, 0, 0, 0, 0, 0, 28, 0, 16, 0, 0, 0, 122, 45, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0];

    assert_eq!(insert_zone_msg_in_buf(test_buf.clone(), 0, 22, String::from("Casserole")), (bytes_4.clone(), 28));
    assert_eq!(insert_zone_msg_in_buf(test_buf.clone(), 0, 22, String::from("")), (bytes_3.clone(), 28));
}
