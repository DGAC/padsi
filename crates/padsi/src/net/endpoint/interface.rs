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
use regex::Regex;
use serde::{Deserialize, Serialize};
use std::{fmt::Display, str::FromStr};

///
/// Network interface
///
#[derive(Debug, PartialEq, Eq, Hash, Deserialize, Serialize)]
pub struct NetworkInterface(pub(crate) String);

impl FromStr for NetworkInterface {
    type Err = Error;

    fn from_str(s: &str) -> std::result::Result<Self, Self::Err> {
        if !s.is_ascii() || s.len() > 15 {
            return Err(anyhow!(
                "Network interface name must be 15 ASCII chars or less"
            ));
        }
        let re = Regex::new(r"^[a-z][a-z0-9-_]*$").unwrap();
        if !re.is_match(s) {
            return Err(anyhow!("Invalid network interface format"));
        }
        Ok(Self(s.into()))
    }
}

impl Display for NetworkInterface {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.0)
    }
}
