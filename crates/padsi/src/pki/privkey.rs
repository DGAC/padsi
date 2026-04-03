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
//! Private key handling
//!
use std::sync::{Arc, RwLock};
use std::{fs, path::Path};

use anyhow::{Result, anyhow};
use rcgen::KeyPair;
use rustls::pki_types::PrivateKeyDer;

///
/// Private key (and its asociated public key)
///
#[derive(Debug)]
pub struct PrivKey {
    pub(super) keypair: KeyPair,
    pub(super) pem: Arc<RwLock<Option<pem::Pem>>>,
}

impl Clone for PrivKey {
    fn clone(&self) -> Self {
        Self {
            keypair: KeyPair::from_pem(&self.keypair.serialize_pem()).unwrap(),
            pem: Arc::new(RwLock::new(None)),
        }
    }
}

impl PartialEq for PrivKey {
    fn eq(&self, other: &Self) -> bool {
        self.keypair.serialize_der() == other.keypair.serialize_der()
    }
}

impl PrivKey {
    pub fn new(keypair: KeyPair) -> Self {
        Self {
            keypair,
            pem: Arc::new(RwLock::new(None)),
        }
    }

    /// Generate a new key pair (using the P-256 curves and SHA-256 hashing as per RFC 5758, PKCS_ECDSA_P256_SHA256)
    pub fn generate() -> Result<Self> {
        Ok(PrivKey {
            keypair: KeyPair::generate()?,
            pem: Arc::new(RwLock::new(None)),
        })
    }

    pub fn from_pem(pem: &str) -> Result<Self> {
        Ok(PrivKey {
            keypair: KeyPair::from_pem(pem)?,
            pem: Arc::new(RwLock::new(None)),
        })
    }

    pub fn pem(&self) -> String {
        self.keypair.serialize_pem()
    }

    /// Write the private key to a file
    pub fn to_file<P: AsRef<Path>>(&self, filename: P) -> Result<(), std::io::Error> {
        fs::write(filename, self.pem())
    }

    /// Load a private key
    pub fn from_file<P: AsRef<Path>>(filename: P) -> Result<Self> {
        let data = fs::read_to_string(filename)?;
        let kp = KeyPair::from_pem(&data)?;
        Ok(Self {
            keypair: kp,
            pem: Arc::new(RwLock::new(None)),
        })
    }

    pub fn privkey_der(&'_ self) -> Result<PrivateKeyDer<'_>> {
        let mut op = self.pem.as_ref().write().unwrap();
        if op.is_none() {
            let p = pem::parse(self.pem())?;
            *op = Some(p);
        }
        let p = op.as_ref().unwrap();
        let private_key: &[_] = p.contents();
        match PrivateKeyDer::try_from(private_key) {
            Ok(k) => Ok(k.clone_key()),
            Err(err) => Err(anyhow!(err)),
        }
    }
}
