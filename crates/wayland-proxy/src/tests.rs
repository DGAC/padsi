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
use crate::constants::{self, WORD_SIZE, WlInterface};
use crate::filter::ProxyState;
use crate::message::{MessageMeta, parse_header};
use crate::proxybuffer::{
    ProxyBuffer, bytes_to_str, create_zone_raw_message, get_zone_tag, search_in_messages_in_vec,
};

#[test]
fn test_read_header() {
    let data_header: Vec<u8> = vec![2, 0, 0, 0, 0, 0, 24, 0, 1];
    let (object_id, opcode, msg_size) = parse_header(&data_header);
    assert_eq!(object_id, 2);
    assert_eq!(opcode, 0);
    assert_eq!(msg_size, 24);
}

#[test]
fn test_read_message() {
    let test_data: Vec<u8> = vec![
        2, 0, 0, 0, 34, 0, 36, 0, 1, 0, 0, 0, 14, 0, 0, 0, 119, 108, 95, 99, 111, 109, 112, 111,
        115, 105, 116, 111, 114, 0, 0, 0, 6, 0, 0, 0, 2, 0, 0, 0, 0, 0, 28, 0, 2, 0, 0, 0, 7, 0, 0,
        0, 119, 108, 95, 100, 114, 109, 0, 0, 2, 0, 0, 0, 2, 0, 0, 0, 0, 0, 28, 0, 3, 0, 0, 0, 7,
        0, 0, 0, 119, 108, 95, 115, 104, 109, 0, 0, 2, 0, 0, 0,
    ];
    let msg = MessageMeta::from_data(&test_data, 0).unwrap();
    assert_eq!(msg.object_id, 2);
    assert_eq!(msg.opcode, 34);
    assert_eq!(msg.size, 36);
    assert_eq!(
        msg.payload(&test_data),
        &[
            1, 0, 0, 0, 14, 0, 0, 0, 119, 108, 95, 99, 111, 109, 112, 111, 115, 105, 116, 111, 114,
            0, 0, 0, 6, 0, 0, 0
        ]
    );
}

#[test]
fn test_read_all_messages() {
    let mut test_data: Vec<u8> = vec![
        2, 0, 0, 0, 0, 0, 36, 0, 1, 0, 0, 0, 14, 0, 0, 0, 119, 108, 95, 99, 111, 109, 112, 111,
        115, 105, 116, 111, 114, 0, 0, 0, 6, 0, 0, 0, 2, 0, 0, 0, 0, 0, 28, 0, 2, 0, 0, 0, 7, 0, 0,
        0, 119, 108, 95, 100, 114, 109, 0, 0, 2, 0, 0, 0, 2, 0, 0, 0, 0, 0, 28, 0, 3, 0, 0, 0, 7,
        0, 0, 0, 119, 108, 95, 115, 104, 109, 0, 0, 2, 0, 0, 0,
    ];
    let msg1 = MessageMeta {
        index: 0,
        size: 36,
        object_id: 2,
        opcode: 0,
    };
    let msg2 = MessageMeta {
        index: msg1.size,
        size: 28,
        object_id: 2,
        opcode: 0,
    };
    let msg3 = MessageMeta {
        index: msg1.size + msg2.size,
        size: 28,
        object_id: 2,
        opcode: 0,
    };

    let pbuffer = ProxyBuffer::from_data(&mut test_data, 92);
    assert_eq!(pbuffer.messages(), vec![msg1, msg2, msg3]);
}

#[test]
fn test_bytes_to_str() {
    let data = vec![
        1, 0, 0, 0, 0, 0, 14, 0, 119, 108, 95, 99, 111, 109, 112, 111, 115, 105, 116, 111, 114, 0,
        0, 0, 0, 0, 0, 6,
    ];
    assert_eq!(
        bytes_to_str(&data),
        Some("\u{1}\0\0\0\0\0\u{e}\0wl_compositor\0\0\0\0\0\0\u{6}")
    );
}

#[test]
fn test_read_all_messages_2() {
    let mut test_data: Vec<u8> = vec![
        1, 0, 0, 0, 1, 0, 12, 0, 2, 0, 0, 0, 1, 0, 0, 0, 0, 0, 12, 0, 3, 0, 0, 0,
    ];
    let msg1 = MessageMeta {
        index: 0,
        size: 12,
        object_id: 1,
        opcode: 1,
    };
    let msg2 = MessageMeta {
        index: msg1.size,
        size: 12,
        object_id: 1,
        opcode: 0,
    };

    let pbuffer = ProxyBuffer::from_data(&mut test_data, 24);
    assert_eq!(pbuffer.messages(), vec![msg1, msg2]);
}

#[test]
fn test_bytes_to_str_2() {
    let data = vec![2, 0, 0, 0];
    assert_eq!(bytes_to_str(&data), Some("\u{2}\0\0\0"));
}

#[test]
fn test_search_for_msg() {
    let data = vec![
        2, 0, 0, 0, 0, 0, 28, 0, 119, 108, 95, 99, 111, 109, 112, 111, 115, 105, 116, 111, 114, 0,
        0, 0, 6, 0, 0, 0, 20, 0, 0, 0, 2, 0, 28, 0, 2, 0, 0, 0, 7, 0, 0, 0, 119, 108, 95, 100, 114,
        109, 0, 0, 2, 0, 0, 0, 22, 0, 0, 0, 8, 0, 4, 0,
    ];
    let msg1 = MessageMeta {
        index: 0,
        size: 28,
        object_id: 2,
        opcode: 0,
    };
    let msg2 = MessageMeta {
        index: msg1.size,
        size: 28,
        object_id: 20,
        opcode: 2,
    };
    let msg3 = MessageMeta {
        index: msg1.size + msg2.size,
        size: 8,
        object_id: 22,
        opcode: 4,
    };
    assert_eq!(
        search_in_messages_in_vec(
            &data,
            &vec![msg1.clone(), msg2.clone(), msg3.clone()],
            Some(2),
            0,
            Some("compo")
        ),
        Some(msg1.clone())
    );
    assert_eq!(
        search_in_messages_in_vec(
            &data,
            &vec![msg1.clone(), msg2.clone(), msg3.clone()],
            Some(2),
            0,
            None
        ),
        Some(msg1.clone())
    );
    assert_eq!(
        search_in_messages_in_vec(
            &data,
            &vec![msg1.clone(), msg2.clone(), msg3.clone()],
            None,
            0,
            Some("Abracadabra")
        ),
        None
    );
    assert_eq!(
        search_in_messages_in_vec(
            &data,
            &vec![msg1.clone(), msg2.clone(), msg3.clone()],
            None,
            4,
            None
        ),
        Some(msg3.clone())
    );
    assert_eq!(
        search_in_messages_in_vec(
            &data,
            &vec![msg1.clone(), msg2.clone(), msg3.clone()],
            None,
            2,
            Some("drm")
        ),
        Some(msg2.clone())
    );
    assert_eq!(
        search_in_messages_in_vec(&data, &vec![msg1, msg2, msg3], Some(23), 0, None),
        None
    );
}

#[test]
fn test_get_global_event_oid() {
    let data = vec![
        1, 0, 0, 0, 0, 0, 16, 0, 42, 0, 0, 0, 40, 0, 0, 0, 2, 0, 0, 0, 0, 0, 48, 0, 7, 0, 0, 0, 23,
        0, 0, 0, 119, 108, 95, 100, 97, 116, 97, 95, 100, 101, 118, 105, 99, 101, 95, 109, 97, 110,
        97, 103, 101, 114, 0, 0, 3, 0, 0, 0, 11, 0, 0, 0,
    ];
    let msg1 = MessageMeta {
        index: 0,
        size: 16,
        object_id: 1,
        opcode: 0,
    };
    let msg2 = MessageMeta {
        index: msg1.size,
        size: 48,
        object_id: 2,
        opcode: 0,
    };

    assert_eq!(
        msg1.extract_referenced_object_id(&data, WORD_SIZE),
        Some(42)
    );
    assert_eq!(msg2.extract_referenced_object_id(&data, 0), Some(11));
    assert_eq!(msg2.extract_referenced_object_id(&data, WORD_SIZE), Some(3));
}

#[test]
fn test_get_global_event_oid_2() {
    let data: Vec<u8> = vec![
        2, 0, 0, 0, 0, 0, 36, 0, 1, 0, 0, 0, 14, 0, 0, 0, 119, 108, 95, 99, 111, 109, 112, 111,
        115, 105, 116, 111, 114, 0, 0, 0, 6, 0, 0, 0, 2, 0, 0, 0, 0, 0, 28, 0, 2, 0, 0, 0, 7, 0, 0,
        0, 119, 108, 95, 100, 114, 109, 0, 0, 2, 0, 0, 0, 2, 0, 0, 0, 0, 0, 28, 0, 3, 0, 0, 0, 7,
        0, 0, 0, 119, 108, 95, 115, 104, 0, 0, 0, 84, 0, 0, 0,
    ];
    let msg = MessageMeta::from_data(&data, 36).unwrap();
    assert_eq!(msg.extract_referenced_object_id(&data, 0), Some(2));

    let msg = MessageMeta::from_data(&data, 0).unwrap();
    assert_eq!(msg.extract_referenced_object_id(&data, 0), Some(6));

    let msg = MessageMeta::from_data(&data, 64).unwrap();
    assert_eq!(msg.extract_referenced_object_id(&data, 0), Some(84));
    assert_eq!(
        msg.extract_referenced_object_id(&data, WORD_SIZE),
        Some(104)
    );
}

#[test]
fn test_matching_messages() {
    let msg1 = MessageMeta {
        index: 0,
        size: 36,
        object_id: 2,
        opcode: 0,
    };
    let msg2 = MessageMeta {
        index: msg1.size,
        size: 12,
        object_id: 1,
        opcode: 1,
    };
    let msg3 = MessageMeta {
        index: msg1.size + msg2.size,
        size: 28,
        object_id: 1,
        opcode: 0,
    };

    let msgs_vec: Vec<MessageMeta> = vec![msg1.clone(), msg2.clone(), msg3.clone()];

    let pstate0 = ProxyState::new(false);

    let mut pstate1 = ProxyState::new(false);
    pstate1.object_created(WlInterface::wl_data_device_manager, 1);

    let mut pstate2 = ProxyState::new(false);
    pstate2.object_created(WlInterface::wl_data_device, 2);

    let mut pstate12 = ProxyState::new(false);
    pstate12.object_created(WlInterface::wl_data_device_manager, 1);
    pstate12.object_created(WlInterface::wl_data_device, 2);

    assert_eq!(pstate0.get_matching_messages(&msgs_vec), vec![]);
    assert_eq!(pstate2.get_matching_messages(&msgs_vec), vec![msg1.clone()]);
    assert_eq!(
        pstate1.get_matching_messages(&msgs_vec),
        vec![msg2.clone(), msg3.clone()]
    );
    assert_eq!(
        pstate12.get_matching_messages(&msgs_vec),
        vec![msg1.clone(), msg2, msg3.clone()]
    );
}

#[test]
fn test_search_message() {
    let data = vec![
        1, 0, 0, 0, 0, 0, 28, 0, 119, 108, 95, 99, 111, 109, 112, 111, 115, 105, 116, 111, 114, 0,
        0, 0, 6, 0, 0, 0, 2, 0, 0, 0, 0, 0, 20, 0, 119, 108, 95, 100, 114, 109, 0, 0, 2, 0, 0, 0,
        4, 0, 0, 0, 0, 0, 20, 0, 119, 108, 95, 115, 104, 0, 0, 0, 2, 0, 0, 0,
    ];

    let msg1 = MessageMeta {
        index: 0,
        size: 28,
        object_id: 1,
        opcode: 0,
    };
    let msg2 = MessageMeta {
        index: msg1.size,
        size: 20,
        object_id: 2,
        opcode: 0,
    };
    let msg3 = MessageMeta {
        index: msg1.size + msg2.size,
        size: 20,
        object_id: 4,
        opcode: 0,
    };

    let msgs_vec = vec![msg1, msg2, msg3];
    let empty_vec: Vec<MessageMeta> = vec![];
    let msg = search_in_messages_in_vec(&data, &msgs_vec, Some(4), 0, Some("sh")).unwrap();
    assert_eq!(msg.extract_referenced_object_id(&data, 0), Some(2));

    let msg = search_in_messages_in_vec(&data, &msgs_vec, None, 0, Some("sh")).unwrap();
    assert_eq!(msg.extract_referenced_object_id(&data, 0), Some(2));

    assert_eq!(
        search_in_messages_in_vec(&data, &msgs_vec, Some(4), 0, Some("sherpa")).is_none(),
        true
    );

    assert_eq!(
        search_in_messages_in_vec(&data, &msgs_vec, Some(3), 0, None).is_none(),
        true
    );

    let msg = search_in_messages_in_vec(&data, &msgs_vec, None, 0, Some("sh")).unwrap();
    assert_eq!(
        msg.extract_referenced_object_id(&data, WORD_SIZE),
        Some(104)
    );

    let msg = search_in_messages_in_vec(&data, &msgs_vec, None, 0, None).unwrap();
    assert_eq!(msg.extract_referenced_object_id(&data, 0), Some(6));

    assert_eq!(
        search_in_messages_in_vec(&data, &msgs_vec, None, 1, None).is_none(),
        true
    );
    assert_eq!(
        search_in_messages_in_vec(&data, &empty_vec, None, 1, None).is_none(),
        true
    );
}

#[test]
fn test_get_oid() {
    let pstate0 = ProxyState::new(false);
    let mut pstate12 = ProxyState::new(false);
    pstate12.object_created(WlInterface::wl_data_device, 1);
    pstate12.object_created(WlInterface::wl_data_device_manager, 2);

    assert_eq!(
        pstate12.get_object_id(&WlInterface::wl_data_device),
        Some(&1)
    );
    assert_eq!(
        pstate12.get_object_id(&WlInterface::wl_data_device_manager),
        Some(&2)
    );
    assert_eq!(pstate12.get_object_id(&WlInterface::wl_data_offer), None);
    assert_eq!(pstate0.get_object_id(&WlInterface::wl_data_source), None);
}

#[test]
fn test_get_msgs_to_block() {
    let msg1 = MessageMeta {
        index: 0,
        size: 36,
        object_id: 2,
        opcode: 0,
    };
    let msg2 = MessageMeta {
        index: msg1.size,
        size: 12,
        object_id: 1,
        opcode: 1,
    };
    let msg3 = MessageMeta {
        index: msg1.size + msg2.size,
        size: 28,
        object_id: 2,
        opcode: 2,
    };

    let msgs_vec: Vec<MessageMeta> = vec![msg1.clone(), msg2.clone(), msg3.clone()];

    let mut pstate = ProxyState::new(true);
    pstate.object_created(WlInterface::wl_data_device, 2);
    pstate.object_created(WlInterface::wl_data_device_manager, 1);

    let mut ps22 = pstate.clone();
    ps22.block_element(false, WlInterface::wl_data_device, 2);

    let mut ps11 = pstate.clone();
    ps11.block_element(false, WlInterface::wl_data_device_manager, 1);

    let mut ps20 = pstate.clone();
    ps20.block_element(false, WlInterface::wl_data_device, 0);

    let mut ps40 = pstate.clone();
    ps40.block_element(false, WlInterface::wl_data_offer, 0);

    let mut ps20_22_11 = pstate.clone();
    ps20_22_11.block_element(false, WlInterface::wl_data_device, 0);
    ps20_22_11.block_element(false, WlInterface::wl_data_device, 2);
    ps20_22_11.block_element(false, WlInterface::wl_data_device_manager, 1);

    assert_eq!(
        ps22.get_messages_to_block(true, false, &msgs_vec),
        vec![msg3.clone()]
    );
    assert_eq!(
        ps11.get_messages_to_block(true, false, &msgs_vec),
        vec![msg2.clone()]
    );
    assert_eq!(
        ps20.get_messages_to_block(true, false, &msgs_vec),
        vec![msg1.clone()]
    );
    assert_eq!(
        ps20_22_11.get_messages_to_block(true, false, &msgs_vec),
        msgs_vec
    );
    assert_eq!(ps40.get_messages_to_block(true, false, &msgs_vec), vec![]);
}

#[test]
fn test_check_for_deleted_obj() {
    let msg1 = MessageMeta {
        index: 0,
        size: 36,
        object_id: 2,
        opcode: 0,
    };
    let msg2 = MessageMeta {
        index: msg1.size,
        size: 12,
        object_id: 1,
        opcode: constants::DS_R_DESTROY,
    };
    let msg3 = MessageMeta {
        index: msg1.size + msg2.size,
        size: 28,
        object_id: 2,
        opcode: constants::DO_R_DESTROY,
    };

    let mut pstate0 = ProxyState::new(true);

    let mut pstate1 = ProxyState::new(true);
    pstate1.object_created(WlInterface::wl_data_offer, 2);

    let mut pstate2 = ProxyState::new(true);
    pstate2.object_created(WlInterface::wl_data_source, 1);

    let mut pstate12 = ProxyState::new(true);
    pstate12.object_created(WlInterface::wl_data_offer, 2);
    pstate12.object_created(WlInterface::wl_data_source, 1);

    let msgs_vec: Vec<MessageMeta> = vec![msg1.clone(), msg2.clone(), msg3.clone()];

    pstate1.handle_destroy_messages(&msgs_vec);
    assert_eq!(pstate1, pstate0);
    pstate2.handle_destroy_messages(&msgs_vec);
    assert_eq!(pstate2, pstate0);
    pstate12.handle_destroy_messages(&msgs_vec);
    assert_eq!(pstate12, pstate0);
    pstate0.handle_destroy_messages(&msgs_vec);
}

#[test]
fn test_object_found() {
    let pstate0 = ProxyState::new(true);
    let mut pstate = ProxyState::new(true);
    pstate.object_created(WlInterface::wl_data_device, 2);
    pstate.object_created(WlInterface::wl_data_device_manager, 1);
    pstate.object_created(WlInterface::wl_data_source, 5);

    assert_eq!(
        pstate.has_interface(&WlInterface::wl_data_device_manager),
        true
    );
    assert_eq!(pstate.has_interface(&WlInterface::wl_data_offer), false);
    assert_eq!(
        pstate.has_interface(&WlInterface::zwp_primary_selection_offer),
        false
    );
    assert_eq!(
        pstate0.has_interface(&WlInterface::wl_data_device_manager),
        false
    );
}

#[test]
fn test_create_zone_msg() {
    let bytes_1: Vec<u8> = vec![
        22, 0, 0, 0, 0, 0, 32, 0, 18, 0, 0, 0, 112, 97, 100, 115, 105, 47, 122, 111, 110, 101, 59,
        122, 61, 116, 101, 115, 116, 0, 0, 0,
    ];
    let bytes_2 = vec![
        231, 3, 0, 0, 0, 0, 32, 0, 18, 0, 0, 0, 112, 97, 100, 115, 105, 47, 122, 111, 110, 101, 59,
        122, 61, 116, 101, 115, 116, 0, 0, 0,
    ];
    let bytes_3 = vec![
        22, 0, 0, 0, 0, 0, 28, 0, 14, 0, 0, 0, 112, 97, 100, 115, 105, 47, 122, 111, 110, 101, 59,
        122, 61, 0, 0, 0,
    ];
    assert_eq!(create_zone_raw_message(22, "test"), bytes_1.clone());
    assert_eq!(create_zone_raw_message(999, "test"), bytes_2.clone());
    assert_eq!(create_zone_raw_message(22, ""), bytes_3.clone());
}

#[test]
fn test_zone_tag() {
    let data = vec![
        24, 0, 0, 0, 0, 0, 36, 0, 22, 0, 0, 0, 112, 97, 100, 115, 105, 47, 122, 111, 110, 101, 59,
        122, 61, 85, 78, 83, 69, 67, 85, 82, 69, 0, 0, 0, 16, 0, 0, 0, 0, 0, 28, 0, 16, 0, 0, 0,
        122, 45, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    ];
    let msg1 = MessageMeta {
        index: 0,
        size: 36,
        object_id: 24,
        opcode: 0,
    };
    let msg2 = MessageMeta {
        index: msg1.size,
        size: 28,
        object_id: 16,
        opcode: 0,
    };

    assert_eq!(
        get_zone_tag(&msg1.payload(&data)),
        Some(String::from("UNSECURE"))
    );
    assert_eq!(get_zone_tag(&msg2.payload(&data)), None);
}
