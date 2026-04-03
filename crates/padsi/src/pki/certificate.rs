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

//!
//! Certificates handling
//!
use anyhow::{Result, anyhow};
use std::{fs, io::Write, path::Path};

use pem_rfc7468::{LineEnding, encode_string};
use rustls_pki_types::{CertificateDer, pem::PemObject};
use x509_parser::prelude::*;

///
/// Certificate's attributes
///
pub struct CertificateAttributes {
    pub cn: String,
}

///
/// Represents a certificate
///
#[derive(Debug, Clone)]
pub struct Certificate {
    pub cert_der: CertificateDer<'static>,
}

impl PartialEq for Certificate {
    fn eq(&self, other: &Self) -> bool {
        self.cert_der == other.cert_der
    }
}

impl Certificate {
    pub fn attributes(&self) -> CertificateAttributes {
        let x509: X509Certificate = X509Certificate::from_der(&self.cert_der).unwrap().1;
        let mut cns: Vec<String> = vec![];
        for cn in x509.subject().iter_common_name() {
            if let Ok(name) = cn.as_str() {
                cns.push(name.into());
            }
        }
        let cn = cns.join(",");
        CertificateAttributes { cn }
    }

    // Create a new certificate from a CertificateDer
    pub fn new(cert_der: CertificateDer<'static>) -> Self {
        Self { cert_der }
    }

    /// PEM representation of the certificate
    pub fn pem(&self) -> String {
        encode_string("CERTIFICATE", LineEnding::CRLF, &self.cert_der).unwrap()
    }

    /// Create object from PEM representation
    pub fn from_pem(pem: &str) -> Result<Self> {
        Ok(Certificate {
            cert_der: CertificateDer::from_pem_slice(pem.as_bytes())?,
        })
    }

    /// Save the certificate to a file
    pub fn to_file<P: AsRef<Path>>(&self, filename: P) -> Result<(), std::io::Error> {
        fs::write(filename, self.pem())
    }

    /// Load a single certificate. If more than one certificate is contained in the file,
    /// then the ones after the first one are ignored.
    pub fn from_file<P: AsRef<Path>>(filename: P) -> Result<Self> {
        let certs: Vec<_> = CertificateDer::pem_file_iter(filename)?.collect();
        match certs.len() {
            0 => Err(anyhow!("No certificate found")),
            _ => {
                let cert = certs[0].as_ref().unwrap();
                Ok(Self::new(cert.clone()))
            }
        }
    }

    /// Load a whole lot of certificates (in the PEM format) from a bundle file
    pub fn load_bundle<P: AsRef<Path>>(filename: P) -> Result<Vec<Self>> {
        let certs = CertificateDer::pem_file_iter(filename)?;
        let mut res: Vec<Self> = vec![];
        for item in certs {
            let cert = &item?;
            res.push(Self::new(cert.clone()));
        }
        Ok(res)
    }

    /// Save a list of certificates to a bunfle file
    pub fn write_bundle<P: AsRef<Path>>(certs: Vec<Self>, filename: P) -> Result<()> {
        let mut file = fs::File::create(filename)?;
        for cert in certs {
            file.write_all(cert.pem().as_bytes())?;
        }
        Ok(())
    }
}
