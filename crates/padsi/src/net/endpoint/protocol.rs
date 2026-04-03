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

use anyhow::{Error, anyhow};
use serde::{Deserialize, Serialize};
use std::{fmt::Display, str::FromStr};

///
/// List of handled low level protocols
///
#[derive(Debug, PartialEq, Deserialize, Serialize)]
#[serde(rename_all = "UPPERCASE")]
pub enum Protocol {
    TCP,
    UDP,
    ICMP,
}

impl FromStr for Protocol {
    type Err = Error;

    fn from_str(s: &str) -> std::result::Result<Self, Self::Err> {
        match &*s.to_lowercase() {
            "tcp" => Ok(Protocol::TCP),
            "udp" => Ok(Protocol::UDP),
            "icmp" => Ok(Protocol::ICMP),
            _ => Err(anyhow!("Unknown protocol '{}'", s)),
        }
    }
}

impl Display for Protocol {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::TCP => write!(f, "tcp"),
            Self::UDP => write!(f, "udp"),
            Self::ICMP => write!(f, "icmp"),
        }
    }
}
