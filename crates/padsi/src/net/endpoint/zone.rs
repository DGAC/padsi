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
use ipnetwork::Ipv4Network;
use serde::{Deserialize, Serialize};
use std::fmt::Display;
use std::{net::Ipv4Addr, str::FromStr};

use super::dname::Name;
use super::dpattern::Pattern;

///
/// "Zone" in an endpoint
///
#[derive(Debug, PartialEq, Eq, Hash, Deserialize, Serialize)]
pub enum Zone {
    All,
    Name(Name),
    Pattern(Pattern),
    Network(Ipv4Network),
}

impl Zone {
    /// Tell if a zone "contains" another zone. Returns None if the test could
    /// not be made (e.g. if self and other represent different kinds of zones)
    pub fn contains(&self, other: &Self) -> Option<bool> {
        match self {
            Self::All => Some(true),
            Self::Name(s_name) => match other {
                Self::Name(o_name) => Some(s_name.contains(o_name)),
                _ => None,
            },
            Self::Pattern(s_pattern) => match other {
                Self::Pattern(o_pattern) => Some(s_pattern.contains(&o_pattern.pattern)),
                Self::Name(o_name) => Some(s_pattern.contains(&o_name.0)),
                _ => None,
            },
            Self::Network(s_net) => match other {
                Self::Network(o_net) => Some(s_net.is_supernet_of(*o_net)),
                _ => None,
            },
        }
    }
}

impl FromStr for Zone {
    type Err = Error;

    fn from_str(s: &str) -> std::result::Result<Self, Self::Err> {
        if s.trim() == "*" {
            return Ok(Self::All);
        }
        match Name::from_str(s) {
            Ok(n) => Ok(Self::Name(n)),
            Err(_) => match Pattern::from_str(s) {
                Ok(p) => Ok(Self::Pattern(p)),
                Err(_) => match Ipv4Network::from_str(s) {
                    Ok(i) => Ok(Self::Network(i)),
                    Err(_) => match Ipv4Addr::from_str(s) {
                        Ok(ip) => Ok(Self::Network(Ipv4Network::new(ip, 32)?)),
                        Err(_) => Err(anyhow!("Invalid zone '{}' format", s)),
                    },
                },
            },
        }
    }
}

impl Display for Zone {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::All => write!(f, "*"),
            Self::Name(n) => write!(f, "{}", n),
            Self::Pattern(p) => write!(f, "{}", p),
            Self::Network(i) => write!(f, "{}", i),
        }
    }
}
