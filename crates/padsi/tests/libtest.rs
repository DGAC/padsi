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

#[cfg(test)]
mod tests {
    use padsi::pki::{usages::TlsServer, CA, PKCS12};
    use std::path::PathBuf;
    use tempfile::NamedTempFile;
    use time::Duration;

    #[test]
    fn ca() {
        // create a CA
        let ca=CA::create("Test CA", Duration::days(3650)).unwrap();
        let p12=ca.to_pkcs12();
        let tmp=NamedTempFile::new().unwrap();
        let password=p12.to_file(tmp.path()).unwrap();

        // load CA from file
        let lp12=PKCS12::from_file(tmp.path(), &password).unwrap();
        assert_eq!(p12, lp12);
        let lca=CA::from_pkcs12(lp12).unwrap();
        assert_eq!(ca, lca);

        // CA's attributes
        let attrs=ca.cert().attributes();
        assert_eq!(attrs.cn, "Test CA");
    }

    #[test]
    fn certs() {
        // create a CA
        let ca=CA::create("Test CA", Duration::days(1)).unwrap();
        println!("CA: {}", ca.cert_pem());

        let tmpl=TlsServer::new(Duration::hours(1));
        let cn=format!("myserver.local");
        let p12=ca.generate_key_and_certificate(&tmpl, &cn, None::<Vec<String>>).unwrap();
        println!("leaf: {}", p12.cert_pem());
        assert_eq!(p12.cert().attributes().cn, "myserver.local");
    }

    #[test]
    fn ext_ca() {
        let mut path = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
        path.push("tests");
        path.push("ca.p12");

        let p12=PKCS12::from_file(path, "i86k2GFMLyomCRQh").unwrap();
        let ca=CA::from_pkcs12(p12).unwrap();

        println!("CA: {}", ca.cert_pem());

        let tmpl=TlsServer::new(Duration::hours(1));
        let cn=format!("myserver.local");
        let p12=ca.generate_key_and_certificate(&tmpl, &cn, None::<Vec<String>>).unwrap();
        println!("leaf: {}", p12.cert_pem());
        assert_eq!(p12.cert().attributes().cn, "myserver.local");
    }

    /*
    #[test]
    fn certs() {
        // create a CA
        let ca=CA::create("Test CA").unwrap();

        let mut cache=CertificatesCache::new(3);
        let tmpl=TlsServer::new(Duration::hours(1));
        for i in 0..5 {
            let cn=format!("entity-{}", i);
            let cert=ca.generate_key_and_certificate(&tmpl, &cn, None::<Vec<String>>).unwrap();
            cache.add(cert.cert().clone());
            if i<3 {
                assert_eq!(i+1, cache.len())
            }
            else {
                assert_eq!(3, cache.len())
            }
        }

        assert_eq!(cache.get("entity-0"), None);
        assert_eq!(cache.get("entity-1"), None);
        let cert=cache.get("entity-3").unwrap();
        assert_eq!(cert.attributes().cn, "entity-3");
    }
    */
}
