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

#[derive(Debug, Clone)]
pub struct ProxyConfig {
    zone: String,
    allowed_zones: Vec<String>,
    allow_no_zone: bool, // allow paste from data copied by a process with no associated zone
    enforce: bool,       // actually enforce blocking
}

impl ProxyConfig {
    pub fn new(zone: &String, allowed_zones_csv: &str, allow_no_zone: bool, enforce: bool) -> Self {
        ProxyConfig {
            zone: zone.clone(),
            allowed_zones: allowed_zones_csv
                .split(",")
                .map(|s| String::from(s))
                .collect(),
            allow_no_zone,
            enforce,
        }
    }

    pub fn zone(&self) -> &String {
        &self.zone
    }

    pub fn zone_is_authorized(&self, zone: &str) -> bool {
        let sz = String::from(zone);
        sz == self.zone || self.allowed_zones.contains(&sz)
    }

    pub fn allow_if_no_zone(&self) -> bool {
        self.allow_no_zone
    }

    pub fn enforce(&self) -> bool {
        self.enforce
    }
}

pub const fn is_little_endian() -> bool {
    #[cfg(target_endian = "big")]
    return false;
    #[cfg(target_endian = "little")]
    return true;
}
