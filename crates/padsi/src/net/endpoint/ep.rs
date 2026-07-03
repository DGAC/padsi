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

use anyhow::{Error, Result, bail};
use ipnetwork::Ipv4Network;
use serde::{Deserialize, Serialize};
use std::fmt::Display;
use std::net::IpAddr;
use std::str::FromStr;
use std::sync::Arc;

use super::dname::Name;
use super::interface::NetworkInterface;
use super::portspec::PortSpec;
use super::protocol::Protocol;
use super::zone::Zone;

///
/// Represent an endpoint in a netflow
///
#[derive(Clone, Debug, PartialEq)]
pub struct EndPoint {
    zones: Arc<Vec<Zone>>,
    interfaces: Arc<Vec<NetworkInterface>>,
    protocols: Arc<Vec<Protocol>>,
    ports: Arc<Vec<PortSpec>>,
}

impl EndPoint {
    /// Create an endpint
    /// The expected format is: <zones> [ '^' <protocols> [ '^' <ports> ] ]
    /// Otherwise, the expected format is: <zones> [ '^' <ports> ]
    ///
    pub fn new(spec: &str) -> Result<Self> {
        let spec = spec.trim();
        let parts: Vec<&str> = spec.split('^').collect();
        if parts.len() > 3 {
            bail!("Too many parts separated by '^'")
        }

        // zones analysis
        let zones_v: Result<Vec<Zone>> = parts[0]
            .split(',')
            .filter(|s| !s.is_empty() && !s.contains("#"))
            .map(|s| Zone::from_str(s.trim()))
            .collect();
        let mut zones_v = zones_v?;

        // zones adjustments
        if zones_v.is_empty() {
            // if there is no zone at all then consider it's "*"
            zones_v.push(Zone::All);
        } else {
            // if Zone::All is present, only keep it
            let mut all_found = false;
            for zone in zones_v.iter() {
                if zone == &Zone::All {
                    all_found = true;
                    break;
                }
            }
            if all_found {
                zones_v = vec![Zone::All];
            }
        }

        // interfaces analysis
        let ifaces_v: Result<Vec<NetworkInterface>> = parts[0]
            .split(',')
            .filter(|s| s.contains("#"))
            .map(|s| NetworkInterface::from_str(&s.trim()[1..]))
            .collect();
        let ifaces_v = ifaces_v?;

        // get the list of protocols and an offset for the ports part
        let (protocols_v, offset): (Vec<Protocol>, usize) = {
            if parts.len() > 1 {
                let res: Result<Vec<Protocol>> = parts[1]
                    .split(',')
                    .filter(|s| *s.trim() != *"")
                    .map(|s| Protocol::from_str(s.trim()))
                    .collect();
                (res?, 1)
            } else {
                (vec![], 1)
            }
        };

        // ports analysis
        let mut ports_v: Vec<PortSpec> = vec![];
        if parts.len() > offset + 1 {
            let res: Result<Vec<PortSpec>> = parts[1 + offset]
                .split(',')
                .map(|s| PortSpec::from_str(s.trim()))
                .collect();
            ports_v = res?;
        }

        Ok(Self {
            zones: Arc::new(zones_v),
            interfaces: Arc::new(ifaces_v),
            protocols: Arc::new(protocols_v),
            ports: Arc::new(ports_v),
        })
    }

    /// Create a new TCP EndPoint from a Web request where the host does not always have a final point
    pub fn new_from_req(s: &str, port: u16) -> Result<Self> {
        if let Ok(_) = IpAddr::from_str(s) {
            return Self::new(&format!("{}^tcp^{}", s, port));
        }
        if let Some(c) = s.chars().nth_back(0)
            && c != '.'
        {
            return Self::new(&format!("{}.^tcp^{}", s, port));
        }
        Self::new(&format!("{}^tcp^{}", s, port))
    }

    /// Get the list of protocols
    /// Returns None if no protocol has been specified
    pub fn protocols(&self) -> Option<&Vec<Protocol>> {
        match self.protocols.len() {
            0 => None,
            _ => Some(&self.protocols),
        }
    }

    /// Get the network interface.
    pub fn interfaces(&self) -> &Vec<NetworkInterface> {
        &self.interfaces
    }

    /// Get all the zones of the endpoint
    pub fn zones(&self) -> &Vec<Zone> {
        &self.zones
    }

    /// Tell if all the zones are IPv4 elements
    pub fn is_ipv4_only(&self) -> bool {
        for zone in self.zones.as_ref() {
            if !matches!(zone, Zone::Network(_)) {
                return false;
            }
        }
        !self.zones.is_empty()
    }

    /// Get all the IPv4 zones of the endpoint
    pub fn zones_ipv4(&self) -> Vec<&Ipv4Network> {
        let mut res: Vec<&Ipv4Network> = vec![];
        for zone in self.zones.as_ref() {
            if let Zone::Network(i) = zone {
                res.push(i);
            }
        }
        res
    }

    /// Tell if the endpoint specified only one or more domain addresses
    pub fn is_names_only(&self) -> bool {
        for zone in self.zones.as_ref() {
            if !matches!(zone, Zone::Name(_)) {
                return false;
            }
        }
        !self.zones.is_empty()
    }

    /// Get all the IPv4 zones of the endpoint
    pub fn zones_names(&self) -> Vec<&Name> {
        let mut res: Vec<&Name> = vec![];
        for zone in self.zones.as_ref() {
            if let Zone::Name(i) = zone {
                res.push(i);
            }
        }
        res
    }

    /// Get the ports (as single port or port ranges)
    pub fn ports(&self) -> &Vec<PortSpec> {
        &self.ports
    }

    /// Tell if the current endpoint "contains" another endpoint, which is the case
    /// when all the possible "variations" of that endpoint (like specific IP addresses in ranges,
    /// specific ports in ports in a ports list or specific domain names in wildcard domains)
    /// are included in the variations of the self endpoint. That is i.e. self offers more
    /// "communications channels" than other.
    ///
    /// Note: when coparing endpoints which specify network interface names, the actual network
    ///     interfaces in the system are not taken into account.
    pub fn contains(&self, other: &Self) -> bool {
        // test interfaces first, quick. If self does not have any interface specification,
        // then it means that any interface is Ok
        if !self.interfaces.is_empty() {
            if !other.interfaces.is_empty() {
                for o_iface in other.interfaces.as_ref() {
                    if !self.interfaces.contains(o_iface) {
                        return false;
                    }
                }
            } else {
                return false;
            }
        }

        // test protocols. If self does not have any protocol specification,
        // then it means that any protocol is Ok
        if !self.protocols.is_empty() {
            // self has some protocols specifications. To be contained, all the other's
            // protocols must be included in the protocols of self.
            if !other.protocols.is_empty() {
                for o_proto in other.protocols.as_ref() {
                    if !self.protocols.contains(o_proto) {
                        return false;
                    }
                }
            } else {
                return false;
            }
        }

        // test ports. If self has no port specification, then it means all the ports are intended
        if !self.ports.is_empty() {
            // self has some ports specifications. To be contained, all the other's ports must be included
            // in the ports of self.
            if !other.ports.is_empty() {
                for o_pspec in other.ports.as_ref() {
                    for o_port in o_pspec.iter() {
                        let mut contained: bool = false;
                        for s_pspec in self.ports.as_ref() {
                            if s_pspec.contains(o_port) {
                                contained = true;
                                break;
                            }
                        }
                        if !contained {
                            return false;
                        }
                    }
                }
            } else {
                return false;
            }
        }

        // finaly test on zones. To be contained, all the other's zones must be "included"
        // as a "subset" of self's zones
        if self.zones.contains(&Zone::All) {
            return true;
        }

        for o_zone in other.zones.as_ref() {
            match o_zone {
                Zone::Name(_) | &Zone::Pattern(_) | &Zone::Network(_) => {
                    let mut contained = false;
                    for s_zone in self.zones.as_ref() {
                        if let Some(b) = s_zone.contains(o_zone)
                            && b
                        {
                            contained = true;
                            break;
                        }
                    }
                    if !contained {
                        return false;
                    }
                }
                _ => {}
            }
        }

        true
    }
}

impl FromStr for EndPoint {
    type Err = Error;

    fn from_str(s: &str) -> std::result::Result<Self, Self::Err> {
        Self::new(s)
    }
}

impl Display for EndPoint {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        let s: Vec<String> = self.zones.iter().map(|item| item.to_string()).collect();
        let mut res = s.join(",");

        for iface in self.interfaces.iter() {
            res.push_str(",#");
            res.push_str(&iface.0);
        }

        if !self.protocols.is_empty() || !self.ports.is_empty() {
            res.push('^');

            let s: Vec<String> = self.protocols.iter().map(|item| item.to_string()).collect();
            res.push_str(&s.join(","));

            if !self.ports.is_empty() {
                res.push('^');
                let s: Vec<String> = self.ports.iter().map(|item| item.to_string()).collect();
                res.push_str(&s.join(","));
            }
        }
        write!(f, "{}", res)
    }
}

impl Serialize for EndPoint {
    fn serialize<S>(&self, serializer: S) -> std::result::Result<S::Ok, S::Error>
    where
        S: serde::Serializer,
    {
        let repr = self.to_string();
        serializer.serialize_str(&repr)
    }
}

impl<'de> Deserialize<'de> for EndPoint {
    fn deserialize<D>(deserializer: D) -> std::result::Result<Self, D::Error>
    where
        D: serde::Deserializer<'de>,
    {
        let s = String::deserialize(deserializer)?;
        Self::from_str(&s).map_err(serde::de::Error::custom)
    }
}
