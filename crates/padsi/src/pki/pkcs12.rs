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
//! PKCS#12 and bundles handling
//!
use anyhow::Result;
use std::ffi::OsStr;
use std::path::Path;
use std::process::Command;

use super::certificate::Certificate;
use super::misc::{generate_password, run_process};
use super::privkey::PrivKey;
use tempfile::NamedTempFile;

///
/// Bundle of a private key, a certificate and optionaly a list of secondary CA certificates
/// as a PKCS#12
///
#[derive(PartialEq, Debug)]
pub struct PKCS12 {
    pub(super) priv_key: PrivKey,
    pub(super) cert: Certificate,
    pub(super) ca_certs: Vec<Certificate>,
}

fn path_to_osstr<P: AsRef<Path>>(path: &P) -> &OsStr {
    path.as_ref().as_os_str()
}

impl PKCS12 {
    /// Write the cryptographic material to a PKCS#12 file and returns the randomly-generated
    /// password which protects the file.
    pub fn to_file<P: AsRef<Path>>(&self, filename: P) -> Result<String> {
        // we use OpenSSL's command line here because the pkcs12 crate is not stable enough
        let key_file = NamedTempFile::new()?;
        self.priv_key.to_file(key_file.path())?;
        let cert_file = NamedTempFile::new()?;
        self.cert.to_file(cert_file.path())?;

        let password = generate_password(15);

        let mut command = Command::new("openssl");
        command
            .arg("pkcs12")
            .arg("-export")
            .arg("-inkey")
            .arg(key_file.path())
            .arg("-in")
            .arg(cert_file.path())
            .arg("-out")
            .arg(path_to_osstr(&filename))
            .arg("-passout")
            .arg("stdin");

        let _output = run_process(command, Some(password.clone()))?;
        Ok(password)
    }

    /// Load a PKCS#12 file
    pub fn from_file<P: AsRef<Path>>(filename: P, password: &str) -> Result<Self> {
        let tmp_file = NamedTempFile::new()?;

        // load private key
        let mut command = Command::new("openssl");
        command
            .arg("pkcs12")
            .arg("-in")
            .arg(path_to_osstr(&filename))
            .arg("-nocerts")
            .arg("-noenc")
            .arg("-out")
            .arg(tmp_file.path())
            .arg("-passin")
            .arg("stdin");
        let _output = run_process(command, Some(password.into()))?;
        let priv_key = PrivKey::from_file(tmp_file.path())?;

        // load certificate
        let mut command = Command::new("openssl");
        command
            .arg("pkcs12")
            .arg("-in")
            .arg(path_to_osstr(&filename))
            .arg("-nokeys")
            .arg("-out")
            .arg(tmp_file.path())
            .arg("-passin")
            .arg("stdin");
        let _output = run_process(command, Some(password.into()))?;
        let cert = Certificate::from_file(tmp_file)?;
        Ok(PKCS12 {
            priv_key,
            cert,
            ca_certs: Vec::new(),
        })
    }

    /// Get the private key
    pub fn priv_key(&self) -> &PrivKey {
        &self.priv_key
    }

    /// Get the certificate
    pub fn cert(&self) -> &Certificate {
        &self.cert
    }

    /// Get the PEM representation of the private key
    pub fn priv_key_pem(&self) -> String {
        self.priv_key.pem()
    }

    /// Get the PEM representation of the certificate
    pub fn cert_pem(&self) -> String {
        self.cert.pem()
    }
}

impl Clone for PKCS12 {
    fn clone(&self) -> Self {
        Self {
            priv_key: self.priv_key.clone(),
            cert: self.cert.clone(),
            ca_certs: self.ca_certs.clone(),
        }
    }
}
