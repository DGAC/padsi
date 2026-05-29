use std::collections::HashMap;
use std::sync::{Arc, Mutex};

use padsi::trace::debug;

use crate::config::ProxyConfig;
use crate::constants::{self, WORD_SIZE, WlInterface};
use crate::message::MessageMeta;
use crate::proxybuffer::ProxyBuffer;

type WlObjectId = u32; // instantiated object
type WlOpCode = u16; // method / event OpCode

#[derive(Debug, PartialEq, Clone)]
pub struct ProxyState {
    /// defined objects
    wl_data_device_manager: Option<WlObjectId>,
    wl_data_device: Option<WlObjectId>,
    wl_data_source: Option<WlObjectId>,
    wl_data_offer: Option<WlObjectId>,

    zwp_primary_selection_device_manager: Option<WlObjectId>,
    zwp_primary_selection_device: Option<WlObjectId>,
    zwp_primary_selection_offer: Option<WlObjectId>,

    /// interface and opcodes of events to block
    core_events_to_block: Vec<(WlInterface, WlOpCode)>,
    x11_events_to_block: Vec<(WlInterface, WlOpCode)>,

    /// opcodes corresponding to the release (destroy) of each interface
    release_opcodes: HashMap<WlInterface, WlOpCode>,
}

impl ProxyState {
    pub fn new(enforce_blocking: bool) -> Self {
        let mut core_events_to_block = vec![];
        let mut x11_events_to_block = vec![];
        if enforce_blocking {
            // Don't block the wl_data_device.data_offer, wl_data_device.selection,
            // zwp_primary_selection_device.data_offer or zwp_primary_selection_device.selection as clients expect them
            // when they get the focus, and will probably crash if not present
            core_events_to_block.push((WlInterface::wl_data_offer, constants::DO_E_OFFER));
            x11_events_to_block.push((
                WlInterface::zwp_primary_selection_offer,
                constants::ZPW_PSO_E_OFFER,
            ));
        }

        // refer to the "destructor" methods in wayland.xml
        let mut release_opcodes = HashMap::new();
        release_opcodes.insert(
            WlInterface::wl_data_device_manager,
            constants::DDM_R_DESTROY,
        );
        release_opcodes.insert(WlInterface::wl_data_device, constants::DD_R_DESTROY);
        release_opcodes.insert(WlInterface::wl_data_source, constants::DS_R_DESTROY);
        release_opcodes.insert(WlInterface::wl_data_offer, constants::DO_R_DESTROY);
        release_opcodes.insert(
            WlInterface::zwp_primary_selection_device_manager,
            constants::ZPW_PDSM_R_DESTROY,
        );
        release_opcodes.insert(
            WlInterface::zwp_primary_selection_device,
            constants::ZPW_PSD_R_DESTROY,
        );
        release_opcodes.insert(
            WlInterface::zwp_primary_selection_offer,
            constants::ZPW_PSO_R_DESTROY,
        );
        Self {
            wl_data_device_manager: None,
            wl_data_device: None,
            wl_data_source: None,
            wl_data_offer: None,

            zwp_primary_selection_device_manager: None,
            zwp_primary_selection_device: None,
            zwp_primary_selection_offer: None,

            core_events_to_block,
            x11_events_to_block,
            release_opcodes,
        }
    }

    #[cfg(test)]
    pub fn block_element(&mut self, is_x11: bool, interface: WlInterface, opcode: WlOpCode) {
        match is_x11 {
            true => self.x11_events_to_block.push((interface, opcode)),
            false => self.core_events_to_block.push((interface, opcode)),
        }
    }

    /// Declare that an object (OID) <-> interface has been defined
    pub fn object_created(&mut self, interface: WlInterface, oid: WlObjectId) {
        match interface {
            WlInterface::wl_data_device_manager => self.wl_data_device_manager = Some(oid),
            WlInterface::wl_data_device => self.wl_data_device = Some(oid),
            WlInterface::wl_data_offer => self.wl_data_offer = Some(oid),
            WlInterface::wl_data_source => self.wl_data_source = Some(oid),
            WlInterface::zwp_primary_selection_device_manager => {
                self.zwp_primary_selection_device_manager = Some(oid)
            }
            WlInterface::zwp_primary_selection_device => {
                self.zwp_primary_selection_device = Some(oid)
            }
            WlInterface::zwp_primary_selection_offer => {
                self.zwp_primary_selection_offer = Some(oid)
            }
        }
    }

    /// Check if a certain interface has been defined
    pub fn has_interface(&self, interface: &WlInterface) -> bool {
        match interface {
            WlInterface::wl_data_device_manager => self.wl_data_device_manager.is_some(),
            WlInterface::wl_data_device => self.wl_data_device.is_some(),
            WlInterface::wl_data_offer => self.wl_data_offer.is_some(),
            WlInterface::wl_data_source => self.wl_data_source.is_some(),
            WlInterface::zwp_primary_selection_device_manager => {
                self.zwp_primary_selection_device_manager.is_some()
            }
            WlInterface::zwp_primary_selection_device => {
                self.zwp_primary_selection_device.is_some()
            }
            WlInterface::zwp_primary_selection_offer => self.zwp_primary_selection_offer.is_some(),
        }
    }

    /// Get an object ID from its interface
    pub fn get_object_id(&self, interface: &WlInterface) -> Option<&WlObjectId> {
        match interface {
            WlInterface::wl_data_device_manager => self.wl_data_device_manager.as_ref(),
            WlInterface::wl_data_device => self.wl_data_device.as_ref(),
            WlInterface::wl_data_offer => self.wl_data_offer.as_ref(),
            WlInterface::wl_data_source => self.wl_data_source.as_ref(),
            WlInterface::zwp_primary_selection_device_manager => {
                self.zwp_primary_selection_device_manager.as_ref()
            }
            WlInterface::zwp_primary_selection_device => self.zwp_primary_selection_device.as_ref(),
            WlInterface::zwp_primary_selection_offer => self.zwp_primary_selection_offer.as_ref(),
        }
    }

    /// Get the interface of an object from its OID
    pub fn get_interface(&self, oid: &WlObjectId) -> Option<&WlInterface> {
        if let Some(eoid) = &self.wl_data_device_manager {
            if oid == eoid {
                return Some(&WlInterface::wl_data_device_manager);
            }
        }
        if let Some(eoid) = &self.wl_data_device {
            if oid == eoid {
                return Some(&WlInterface::wl_data_device);
            }
        }
        if let Some(eoid) = &self.wl_data_offer {
            if oid == eoid {
                return Some(&WlInterface::wl_data_offer);
            }
        }
        if let Some(eoid) = &self.wl_data_source {
            if oid == eoid {
                return Some(&WlInterface::wl_data_source);
            }
        }
        if let Some(eoid) = &self.zwp_primary_selection_device_manager {
            if oid == eoid {
                return Some(&WlInterface::zwp_primary_selection_device_manager);
            }
        }
        if let Some(eoid) = &self.zwp_primary_selection_device {
            if oid == eoid {
                return Some(&WlInterface::zwp_primary_selection_device);
            }
        }
        if let Some(eoid) = &self.zwp_primary_selection_offer {
            if oid == eoid {
                return Some(&WlInterface::zwp_primary_selection_offer);
            }
        }
        None
    }

    /// Actually delete an object
    fn delete_object_id(&mut self, oid: &WlObjectId) {
        if let Some(eoid) = &self.wl_data_device_manager {
            if oid == eoid {
                self.wl_data_device_manager = None;
                return;
            }
        }
        if let Some(eoid) = &self.wl_data_device {
            if oid == eoid {
                self.wl_data_device = None;
                return;
            }
        }
        if let Some(eoid) = &self.wl_data_offer {
            if oid == eoid {
                self.wl_data_offer = None;
                return;
            }
        }
        if let Some(eoid) = &self.wl_data_source {
            if oid == eoid {
                self.wl_data_source = None;
                return;
            }
        }
        if let Some(eoid) = &self.zwp_primary_selection_device_manager {
            if oid == eoid {
                self.zwp_primary_selection_device_manager = None;
                return;
            }
        }
        if let Some(eoid) = &self.zwp_primary_selection_device {
            if oid == eoid {
                self.zwp_primary_selection_device = None;
                return;
            }
        }
        if let Some(eoid) = &self.zwp_primary_selection_offer {
            if oid == eoid {
                self.zwp_primary_selection_offer = None;
                return;
            }
        }
    }

    pub fn get_matching_messages(&self, msgs: &Vec<MessageMeta>) -> Vec<MessageMeta> {
        let mut match_msgs: Vec<MessageMeta> = vec![];
        for msg in msgs {
            if let Some(_) = self.get_interface(&msg.object_id) {
                match_msgs.push(msg.clone());
            }
        }
        match_msgs
    }

    /// Get the messages to block
    pub fn get_messages_to_block(
        &self,
        block_core: bool,
        block_x11: bool,
        msgs: &Vec<MessageMeta>,
    ) -> Vec<MessageMeta> {
        let mut msgs_to_block: Vec<MessageMeta> = vec![];
        for msg in msgs {
            if let Some(interface) = self.get_interface(&msg.object_id) {
                let mut nb: usize = 0;
                if block_core {
                    nb = self
                        .core_events_to_block
                        .iter()
                        .filter(|(iface, opcode)| iface == interface && *opcode == msg.opcode)
                        .count();
                }
                if nb == 0 && block_x11 {
                    nb = self
                        .x11_events_to_block
                        .iter()
                        .filter(|(iface, opcode)| iface == interface && *opcode == msg.opcode)
                        .count();
                }
                if nb > 0 {
                    msgs_to_block.push(msg.clone());
                }
            }
        }
        msgs_to_block
    }

    /// Checks for any object which might be destroyed
    pub fn handle_destroy_messages(&mut self, msgs: &Vec<MessageMeta>) {
        for msg in msgs {
            if let Some(iface) = self.get_interface(&msg.object_id) {
                if let Some(destroy_opcode) = self.release_opcodes.get(iface) {
                    if *destroy_opcode == msg.opcode {
                        self.delete_object_id(&msg.object_id);
                    }
                }
            }
        }
    }
}

/// Forward and maybe modify client to server messages
pub fn proxy_client_to_server(
    sconfig: &Arc<Mutex<ProxyConfig>>,
    proxy_state: &Arc<Mutex<ProxyState>>,
    data: &mut Vec<u8>,
    ndata: usize,
) -> usize {
    let mut pbuffer = ProxyBuffer::from_data(data, ndata);
    let mut proxy_state = proxy_state.lock().unwrap();
    let conf = sconfig.lock().unwrap();

    pbuffer.trace("C2S");

    // get wl_data_device_manager if not yet found
    let mut o_wl_ddm_oid = proxy_state
        .get_object_id(&WlInterface::wl_data_device_manager)
        .cloned();
    if o_wl_ddm_oid.is_none() {
        if let Some(oid) = pbuffer.search_referenced_object_id(
            None,
            0,
            Some(WlInterface::wl_data_device_manager.id()),
            0,
        ) {
            debug!(
                way = "C2S",
                zone = conf.zone(),
                "wl_data_device_manager OID:{}",
                oid
            );
            proxy_state.object_created(WlInterface::wl_data_device_manager, oid);
            o_wl_ddm_oid = Some(oid)
        }
    }

    // get wl_data_device if not yet found
    if o_wl_ddm_oid.is_some() && !proxy_state.has_interface(&WlInterface::wl_data_device) {
        if let Some(wl_dd_oid) = pbuffer.search_referenced_object_id(
            o_wl_ddm_oid,
            constants::DDM_R_GET_DATA_DEVICE,
            None,
            WORD_SIZE,
        ) {
            debug!(
                way = "C2S",
                zone = conf.zone(),
                "wl_data_device OID: {}",
                wl_dd_oid
            );
            proxy_state.object_created(WlInterface::wl_data_device, wl_dd_oid);
        }
    }

    // get the zwp_primary_selection_device_manager if not yet found
    let mut o_zwp_psdm_oid = proxy_state
        .get_object_id(&WlInterface::zwp_primary_selection_device_manager)
        .cloned();
    if o_zwp_psdm_oid.is_none() {
        if let Some(oid) = pbuffer.search_referenced_object_id(
            None,
            0,
            Some(WlInterface::zwp_primary_selection_device_manager.id()),
            0,
        ) {
            debug!(
                way = "C2S",
                zone = conf.zone(),
                "zwp_primary_selection_device_manager OID: {}",
                oid
            );
            proxy_state.object_created(WlInterface::zwp_primary_selection_device_manager, oid);
            o_zwp_psdm_oid = Some(oid)
        }
    }

    // get for the zwp_primary_selection_device if not yet found
    if o_zwp_psdm_oid.is_some()
        && !proxy_state.has_interface(&WlInterface::zwp_primary_selection_device)
    {
        if let Some(zwp_psd_oid) = pbuffer.search_referenced_object_id(
            o_zwp_psdm_oid,
            constants::ZPW_PDSM_R_GET_DEVICE,
            None,
            WORD_SIZE,
        ) {
            debug!(
                way = "C2S",
                zone = conf.zone(),
                "zwp_primary_selection_device OID: {}",
                zwp_psd_oid
            );
            proxy_state.object_created(WlInterface::zwp_primary_selection_device, zwp_psd_oid);
        }
    }

    if o_wl_ddm_oid.is_some() {
        let mut o_wl_ds_oid = proxy_state
            .get_object_id(&WlInterface::wl_data_source)
            .cloned();
        if o_wl_ds_oid.is_none() {
            // search for a wl_data_source object
            if let Some(oid) = pbuffer.search_referenced_object_id(
                o_wl_ddm_oid,
                constants::DDM_R_CREATE_DATA_SOURCE,
                None,
                0,
            ) {
                debug!(
                    way = "C2S",
                    zone = conf.zone(),
                    "wl_data_source OID: {}",
                    oid
                );
                proxy_state.object_created(WlInterface::wl_data_source, oid);
                o_wl_ds_oid = Some(oid)
            }
        }

        if o_wl_ds_oid.is_some() {
            // search for the wl_data_offer to inject the zone's ID
            if let Some(wl_ds_offer) = pbuffer.search(o_wl_ds_oid, constants::DS_R_OFFER, None) {
                pbuffer.debug_a_message(&wl_ds_offer, "wl_data_offer");
                pbuffer.insert_zone_name_tag(wl_ds_offer.index, o_wl_ds_oid.unwrap(), &conf.zone());
                debug!(
                    way = "C2S",
                    zone = conf.zone(),
                    "Inserted zone ID '{}' msg",
                    &conf.zone()
                );
            }
        }

        if let Some(wl_do_oid) = proxy_state.get_object_id(&WlInterface::wl_data_offer) {
            // search for the wl_data_offer.receive event used when client is requesting paste
            if let Some(wl_ds_offer) =
                pbuffer.search(Some(*wl_do_oid), constants::DO_R_RECEIVE, None)
            {
                // may be used in the future to ask permission from the user before allowing paste. This feature will require that wl_data_offer.offer events are not blocked
                pbuffer.debug_a_message(&wl_ds_offer, "wl_data_offer.receive request")
            }
        }

        let msgs_i: Vec<MessageMeta> = pbuffer.matching_messages(&proxy_state);
        if !msgs_i.is_empty() {
            proxy_state.handle_destroy_messages(&msgs_i);
        }
    }

    pbuffer.len()
}

/// Forward and maybe modify server to client messages
pub fn proxy_server_to_client(
    sconfig: &Arc<Mutex<ProxyConfig>>,
    proxy_state: &Arc<Mutex<ProxyState>>,
    data: &mut Vec<u8>,
    ndata: usize,
) -> usize {
    let mut pbuffer = ProxyBuffer::from_data(data, ndata);
    let mut proxy_state = proxy_state.lock().unwrap();

    pbuffer.trace("S2C");

    if proxy_state.has_interface(&WlInterface::wl_data_device_manager) {
        let conf = sconfig.lock().unwrap();
        let mut block_core: bool = false;
        let mut block_x11: bool = false;

        if let Some(wl_dd_oid) = proxy_state.get_object_id(&WlInterface::wl_data_device) {
            // search for the wl_data_offer object
            if let Some(wl_do_oid) = pbuffer.search_referenced_object_id(
                Some(*wl_dd_oid),
                constants::DD_E_DATA_OFFER,
                None,
                0,
            ) {
                debug!(
                    way = "S2C",
                    zone = conf.zone(),
                    "wl_data_offer OID: {}",
                    wl_do_oid
                );
                proxy_state.object_created(WlInterface::wl_data_offer, wl_do_oid);

                if let Some(zone_name) = pbuffer.search_zone_name_tag(wl_do_oid) {
                    let authz = conf.zone_is_authorized(&zone_name);
                    if !authz {
                        block_core = true;
                    }
                    debug!(
                        way = "S2C",
                        zone = conf.zone(),
                        "Message from zone '{zone_name}', is authorized: {}",
                        authz
                    )
                } else {
                    block_core = !conf.allow_if_no_zone();
                    debug!(
                        way = "S2C",
                        zone = conf.zone(),
                        "No zone in message from compositor, is authorized: {}",
                        !block_core
                    )
                }
            }
        }

        // Search for primary selection offer
        if let Some(zwp_psd_oid) =
            proxy_state.get_object_id(&WlInterface::zwp_primary_selection_device)
        {
            if let Some(zwp_o_oid) = pbuffer.search_referenced_object_id(
                Some(*zwp_psd_oid),
                constants::ZPW_PSD_E_DATA_OFFER,
                None,
                0,
            ) {
                debug!(
                    way = "S2C",
                    zone = conf.zone(),
                    "zwp_primary_selection_offer OID: {}",
                    zwp_o_oid
                );
                proxy_state.object_created(WlInterface::zwp_primary_selection_offer, zwp_o_oid);
                block_x11 = true; // we can't know from which zone the primary selection (X11) data offer comes from, so block paste
            }
        }

        if conf.enforce() && (block_core || block_x11) {
            let msgs_i: Vec<MessageMeta> = pbuffer.matching_messages(&proxy_state);
            if !msgs_i.is_empty() {
                let msgs_to_block =
                    proxy_state.get_messages_to_block(block_core, block_x11, &msgs_i);
                if msgs_to_block.len() > 0 {
                    //pbuffer.debug(&format!("S2C '{}', before block (core: {}, x11: {})", conf.zone(), block_core, block_x11));
                    pbuffer.debug_messages(
                        &msgs_to_block,
                        &format!("S2C '{}', to block", conf.zone()),
                    );
                    pbuffer.remove_messages(msgs_to_block);
                    //pbuffer.debug(&format!("S2C '{}', after block", conf.zone()));
                }
            }
        }
    }
    pbuffer.len()
}
