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
//! Module to implement a simple PKI via certification authorities
//!
//! Example:
//! ```
//! use time::Duration;
//! use padsi::pki::{CA, PrivKey, Certificate, PKCS12, usages::TlsServer};
//!
//! // Root CA
//! let root_ca=CA::create("My CA", Duration::days(1)).unwrap();
//! println!("CA priv key:\n{}", root_ca.priv_key_pem());
//! println!("CA certificate:\n{}", root_ca.cert_pem());
//!
//! // a TLS cert
//! let tmpl=TlsServer::new(Duration::hours(1));
//! let pkcs12=root_ca.generate_key_and_certificate(&tmpl, "server.local",
//! Some(vec!["second.local".into()])).unwrap();
//!
//! println!("Leaf priv key:\n{}", pkcs12.priv_key_pem());
//! println!("Leaf certificate:\n{}", pkcs12.cert_pem());
//!
//! pkcs12.priv_key().to_file("leaf.key").unwrap();
//! pkcs12.cert().to_file("leaf.crt").unwrap();
//!
//! let l_key=PrivKey::from_file("leaf.key").unwrap();
//! assert_eq!(&l_key, pkcs12.priv_key());
//! let l_cert=Certificate::from_file("leaf.crt").unwrap();
//! assert_eq!(&l_cert, pkcs12.cert());
//!
//! let password=pkcs12.to_file("leaf.p12").unwrap();
//! println!("Password: {password}");
//! let l_pkcs12=PKCS12::from_file("leaf.p12", &password).unwrap();
//! assert_eq!(l_pkcs12, pkcs12);
//!
//! std::fs::remove_file("leaf.key");
//! std::fs::remove_file("leaf.crt");
//! std::fs::remove_file("leaf.p12");
//! ```
pub mod ca;
pub mod certificate;
pub mod misc;
pub mod pkcs12;
pub mod privkey;
pub mod usages;

pub use ca::CA;
pub use certificate::Certificate;
pub use pkcs12::PKCS12;
pub use privkey::PrivKey;
