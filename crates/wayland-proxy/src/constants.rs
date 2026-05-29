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

pub const WORD_SIZE: usize = 4; // Wayland specifies a word as 32 bits
pub const HEADER_SIZE: usize = 2 * WORD_SIZE; // Wayand message header size

macro_rules! enum_str {
    (enum $name:ident {
        $($variant:ident),*,
    }) => {
        ///
        /// Wayland interfaces, non camel case to better refer to
        /// the protocols
        ///
        #[allow(non_camel_case_types)]
        #[derive(Debug,Eq,PartialEq,Hash,Clone)]
        pub enum $name {
            $($variant),*
        }

        impl $name {
            pub fn id(&self) -> &'static str {
                match self {
                    $($name::$variant => stringify!($variant)),*
                }
            }
        }
    };
}

enum_str! {
    enum WlInterface {
        // core Wayland protocol
        wl_data_device_manager,
        wl_data_device,
        wl_data_offer,
        wl_data_source,
        // primary selection protocol
        zwp_primary_selection_device_manager,
        zwp_primary_selection_device,
        zwp_primary_selection_offer,
    }
}

// Opcodes constants naming convention:
//
// Events: <interface>_E_<event>
// Requests: <interface>_R_<request>
//
// (refer to https://wayland.app/protocols/)

// wl_data_device_manager
pub const DDM_R_CREATE_DATA_SOURCE: u16 = 0;
pub const DDM_R_GET_DATA_DEVICE: u16 = 1;
pub const DDM_R_DESTROY: u16 = 2;

// wl_data_device
pub const DD_E_DATA_OFFER: u16 = 0;
pub const DD_R_DESTROY: u16 = 2;

// wl_data_source
pub const DS_R_OFFER: u16 = 0;
pub const DS_R_DESTROY: u16 = 1;

// wl_data_offer
pub const DO_R_RECEIVE: u16 = 1;
pub const DO_R_DESTROY: u16 = 2;
pub const DO_E_OFFER: u16 = 0;

// zwp_primary_selection_device_manager_v1
pub const ZPW_PDSM_R_GET_DEVICE: u16 = 1;
pub const ZPW_PDSM_R_DESTROY: u16 = 2;

// zwp_primary_selection_device
pub const ZPW_PSD_E_DATA_OFFER: u16 = 0;
pub const ZPW_PSD_R_DESTROY: u16 = 1;

// zwp_primary_selection_offer
pub const ZPW_PSO_E_OFFER: u16 = 0;
pub const ZPW_PSO_R_DESTROY: u16 = 1;
