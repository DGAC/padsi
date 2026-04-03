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
use std::{fmt::Display, str::FromStr};

///
/// Ports and port ranges
///
#[derive(Debug, PartialEq, Deserialize, Serialize)]
pub enum PortSpec {
    Port(u16),       // iterator value, port number
    Range(u16, u16), // iterator value, port range
}

impl PortSpec {
    pub fn new_port(port: u16) -> Result<Self> {
        if port == 0 {
            Err(anyhow!("Invalid port 0"))
        } else {
            Ok(Self::Port(port))
        }
    }

    pub fn new_range(start: u16, end: u16) -> Result<Self> {
        if start == end {
            Self::new_port(start)
        } else if start > end {
            Err(anyhow!("Invalid port range {} - {}", start, end))
        } else {
            Ok(Self::Range(start, end))
        }
    }

    /// Tell if a port number is contained in self
    pub fn contains(&self, port: u16) -> bool {
        match self {
            Self::Port(p) => *p == port,
            Self::Range(a, b) => *a <= port && port <= *b,
        }
    }

    /// Get an iterator
    pub fn iter(&self) -> PortSpecIterator<'_> {
        PortSpecIterator {
            portspec: self,
            index: None,
        }
    }
}

pub struct PortSpecIterator<'a> {
    portspec: &'a PortSpec,
    index: Option<u16>,
}

impl<'a> Iterator for PortSpecIterator<'a> {
    type Item = u16;

    fn next(&mut self) -> Option<Self::Item> {
        match self.portspec {
            PortSpec::Port(p) => match self.index {
                Some(_) => None,
                None => {
                    self.index = Some(*p);
                    self.index
                }
            },
            PortSpec::Range(a, b) => match self.index.as_mut() {
                Some(x) => {
                    if *x == *b {
                        None
                    } else {
                        *x += 1;
                        Some(*x)
                    }
                }
                None => {
                    self.index = Some(*a);
                    self.index
                }
            },
        }
    }
}

impl FromStr for PortSpec {
    type Err = Error;

    fn from_str(s: &str) -> std::result::Result<Self, Self::Err> {
        let parts: Vec<&str> = s.split('-').collect();
        match parts.len() {
            1 => Ok(PortSpec::Port(parts[0].parse()?)),
            2 => {
                let a: u16 = parts[0].parse()?;
                let b: u16 = parts[1].parse()?;
                if a == 0 || b == 0 || a > b {
                    return Err(anyhow!("Invalid port range '{}'", parts[1]));
                }
                if a == b {
                    Ok(PortSpec::Port(a))
                } else {
                    Ok(PortSpec::Range(a, b))
                }
            }
            _ => Err(anyhow!("Invalid port specification '{}'", s)),
        }
    }
}

impl Display for PortSpec {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Port(p) => write!(f, "{}", p),
            Self::Range(a, b) => write!(f, "{}-{}", a, b),
        }
    }
}
