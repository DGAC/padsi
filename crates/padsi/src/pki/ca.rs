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
//! Certification authority
//!
use anyhow::{anyhow, Result};
use time::{OffsetDateTime, Duration};
use rcgen::{CertificateParams, DnType, Issuer, KeyPair, KeyUsagePurpose};
use x509_parser::prelude::{X509Certificate, oid_registry, FromDer};

use super::certificate::Certificate;
use super::pkcs12::PKCS12;
use super::privkey::PrivKey;
use super::usages::CertificateUsage;
use crate::trace::{debug};

///
/// Certification Authority object
///
#[derive(Clone, PartialEq, Debug)]
pub struct CA {
    /// CA's private key
    priv_key: PrivKey,

    /// CA's certificate
    cert: Certificate,
}

impl CA {
    /// Initialize a new CA
    pub fn create(cn: &str, duration:Duration) -> Result<Self> {
        let priv_key = PrivKey::generate()?;
        let mut ca_params = CertificateParams::default();
        ca_params.is_ca = rcgen::IsCa::Ca(rcgen::BasicConstraints::Unconstrained);
        ca_params.not_before=OffsetDateTime::now_utc();
        ca_params.not_after=ca_params.not_before+duration;
        ca_params.distinguished_name.push(DnType::CommonName, cn);
        let ca_cert = ca_params.self_signed(&priv_key.keypair)?;
        Ok(Self {
            priv_key,
            cert: Certificate::new(ca_cert.der().clone()),
        })
    }

    /// Get the CA's certificate
    pub fn cert(&self) -> &Certificate {
        &self.cert
    }

    /// Get the CA's private key in the PEM format
    pub fn priv_key_pem(&self) -> String {
        self.priv_key.pem()
    }

    /// Get the CA's certificate in the PEM format
    pub fn cert_pem(&self) -> String {
        self.cert.pem()
    }

    fn complement_certificate_params(&self, cert_params: &mut CertificateParams) -> Result<()>{
        let (_, parsed) = X509Certificate::from_der(&self.cert.cert_der).unwrap();
        for rdn in parsed.tbs_certificate.subject.iter() {
            println!("... {:?}", rdn);
            for attr in rdn.iter() {
                let (oid, value) = (attr.attr_type(), attr.attr_value());
                println!("+==> {:?} = {:?}", oid, value.as_str().unwrap());
                let dntype= if *oid==oid_registry::OID_X509_COUNTRY_NAME {
                    DnType::CountryName
                } else if *oid==oid_registry::OID_X509_LOCALITY_NAME {
                    DnType::CountryName
                } else if *oid==oid_registry::OID_X509_STATE_OR_PROVINCE_NAME {
                    DnType::StateOrProvinceName
                } else if *oid==oid_registry::OID_X509_ORGANIZATION_NAME {
                    DnType::OrganizationName
                } else if *oid==oid_registry::OID_X509_ORGANIZATIONAL_UNIT {
                    DnType::OrganizationalUnitName
                } else if *oid==oid_registry::OID_X509_COMMON_NAME {
                    DnType::CommonName
                } else {
                    return Err(anyhow!("Unhandled OID {} in DN", oid))
                };
                cert_params.distinguished_name.push(dntype, value.as_str().unwrap());
            }
        }
        Ok(())
    }

    /// Generate a private key and a public key and a certificate for an entity
    /// and returns a PKCS#12 object
    pub fn generate_key_and_certificate<T: CertificateUsage>(&self, usage: &T, cn: &str, other_names: Option<impl Into<Vec<String>>>) -> Result<PKCS12> {
        // entity's associated date
        let ent_key = KeyPair::generate()?;
        //let ent_key = KeyPair::generate()?;
        let mut ent_params = CertificateParams::default();
        usage.get_params(&mut ent_params, cn, other_names)?;
        ent_params.is_ca=rcgen::IsCa::ExplicitNoCa;

        // prepare CA side
        let mut ca_params = CertificateParams::default();
        ca_params.is_ca = rcgen::IsCa::Ca(rcgen::BasicConstraints::Unconstrained);
        self.complement_certificate_params(&mut ca_params)?;
        ca_params.key_usages = vec![
            KeyUsagePurpose::KeyCertSign,
            KeyUsagePurpose::CrlSign,
        ];
        let signing_key = Issuer::new(ca_params, &self.priv_key.keypair);

        // generate certificate
        let ent_cert = ent_params.signed_by(&ent_key, &signing_key)?;

        debug!("CA '{}' generated a '{}' certificate for '{}'", self.cert.attributes().cn, usage.name(), cn);
        Ok(PKCS12 {
            priv_key: PrivKey::new(ent_key),
            cert: Certificate::new(ent_cert.der().clone()),
            ca_certs: Vec::new(),
        })
    }

    /// Export the private key an the certificate as a PKCS#12 object
    pub fn to_pkcs12(&self) -> PKCS12 {
        PKCS12 {
            priv_key: PrivKey::from_pem(&self.priv_key.pem()).unwrap(),
            cert: Certificate::from_pem(&self.cert.pem()).unwrap(),
            ca_certs: Vec::new(),
        }
    }

    /// Create a CA object using the private key and certificate in
    /// a PKCS#12 object
    pub fn from_pkcs12(p12: PKCS12) -> Result<Self> {
        Ok(Self {
            priv_key: PrivKey::from_pem(&p12.priv_key.pem())?,
            cert: Certificate::from_pem(&p12.cert.pem())?,
        })
    }
}
