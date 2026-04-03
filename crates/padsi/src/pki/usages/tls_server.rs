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

use std::net::IpAddr;
use std::str::FromStr;
use anyhow::Result;
use time::{OffsetDateTime, Duration};
use rcgen::{string::Ia5String, DnType, ExtendedKeyUsagePurpose, IsCa, KeyUsagePurpose, SanType };
use super::CertificateUsage;

///
/// Usage of certificates for TLS server
///
pub struct TlsServer {
    /// Validity period of the certificate
    duration: Duration
}

impl TlsServer {
    pub fn new(duration: Duration) -> Self {
        Self {
            duration
        }
    }
}

impl CertificateUsage for TlsServer {
    fn name(&self) -> &'static str {
        "TlsServer"
    }

    fn get_params(&self, builder: &mut rcgen::CertificateParams, cn:&str,
        other_names:Option<impl Into<Vec<String>>>) -> Result<()> {
        builder.not_before=OffsetDateTime::now_utc();
        builder.not_after=builder.not_before+self.duration;
        builder.is_ca=IsCa::NoCa;
        builder.key_usages=vec![KeyUsagePurpose::DigitalSignature, KeyUsagePurpose::KeyEncipherment];
        builder.extended_key_usages=vec![ExtendedKeyUsagePurpose::ServerAuth];
        builder.distinguished_name.push(DnType::CommonName, cn);

        // add CN as 1st item of SAN
        let item=match IpAddr::from_str(cn) {
            Ok(ip) => SanType::IpAddress(ip),
            Err(_) => SanType::DnsName(Ia5String::from_str(cn)?),
        };
        let mut san:Vec<SanType>=vec![item];

        // add other names as SAN items
        if let Some(other_names)=other_names {
            for name in other_names.into() {
                let item=match IpAddr::from_str(&name) {
                    Ok(ip) => SanType::IpAddress(ip),
                    Err(_) => SanType::DnsName(Ia5String::from_str(&name)?)
                };
                san.push(item);
            }
        }

        builder.subject_alt_names=san;
        Ok(())
    }
}
