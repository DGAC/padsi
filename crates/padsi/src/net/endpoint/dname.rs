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

use anyhow::{Error, Result, anyhow};
use serde::{Deserialize, Serialize};
use std::{fmt::Display, hash::Hash, str::FromStr};

///
/// Domain name
///
#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct Name(pub(crate) String);

impl Name {
    pub fn new(name: &str) -> Result<Self> {
        // TODO: better check validity (refer to Python's _is_domain_name())
        match name.chars().nth_back(0) {
            Some('.') => {
                if name.contains("*") {
                    Err(anyhow!("Name must not contain any '*'"))
                } else {
                    Ok(Self(name.to_string()))
                }
            }
            _ => Err(anyhow!("Must end with a '.'")),
        }
    }

    /// Test if the Name "contains" another Name (i.e. is a superset of it)
    pub fn contains(&self, other: &Self) -> bool {
        self.0 == other.0
    }

    pub fn without_trailing_dot(&self) -> &str {
        &self.0[..self.0.len() - 1]
    }
}

impl FromStr for Name {
    type Err = Error;

    fn from_str(s: &str) -> std::result::Result<Self, Self::Err> {
        Self::new(s)
    }
}

impl Display for Name {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.0)
    }
}

impl PartialEq for Name {
    fn eq(&self, other: &Self) -> bool {
        self.0 == other.0
    }
}

impl Eq for Name {}

impl Hash for Name {
    fn hash<H: std::hash::Hasher>(&self, state: &mut H) {
        self.0.hash(state);
    }
}
