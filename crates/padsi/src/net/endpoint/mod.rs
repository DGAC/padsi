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

mod dname;
mod dpattern;
mod ep;
mod interface;
mod portspec;
mod protocol;
mod zone;

pub use dname::Name;
pub use dpattern::Pattern;
pub use ep::EndPoint;
pub use interface::NetworkInterface;
pub use portspec::PortSpec;
pub use protocol::Protocol;
pub use zone::Zone;

#[cfg(test)]
mod tests {
    use std::str::FromStr;

    use ipnetwork::Ipv4Network;

    use super::*;

    #[test]
    fn endpoints() {
        let ep = EndPoint::from_str("").unwrap();
        assert_eq!(ep.zones().len(), 1);
        assert_eq!(ep.to_string(), "*");
        assert_eq!(ep.is_names_only(), false);
        assert_eq!(ep.is_ipv4_only(), false);
        assert_eq!(ep.zones(), &vec![Zone::All]);

        let ep = EndPoint::from_str("*").unwrap();
        assert_eq!(ep.to_string(), "*");
        assert_eq!(ep.is_names_only(), false);
        assert_eq!(ep.is_ipv4_only(), false);
        assert_eq!(ep.zones(), &vec![Zone::All]);

        let ep = EndPoint::from_str("*^^443").unwrap();
        assert_eq!(ep.to_string(), "*^^443");
        assert_eq!(ep.is_names_only(), false);
        assert_eq!(ep.is_ipv4_only(), false);
        assert_eq!(ep.zones(), &vec![Zone::All]);

        let ep = EndPoint::new("*^  tcp").unwrap();
        assert_eq!(ep.to_string(), "*^tcp");
        assert_eq!(ep.is_names_only(), false);
        assert_eq!(ep.is_ipv4_only(), false);
        assert_eq!(ep.zones(), &vec![Zone::All]);

        let ep = EndPoint::new("* ^ TCP ^ 1,445-446,567-678").unwrap();
        assert_eq!(ep.to_string(), "*^tcp^1,445-446,567-678");
        assert_eq!(
            ep.ports(),
            &vec![
                PortSpec::new_port(1).unwrap(),
                PortSpec::new_range(445, 446).unwrap(),
                PortSpec::new_range(567, 678).unwrap()
            ]
        );

        let ep = EndPoint::from_str("* ^ tcp^ 1,445-446,567-678").unwrap();
        assert_eq!(ep.to_string(), "*^tcp^1,445-446,567-678");
        assert_eq!(
            ep.ports(),
            &vec![
                PortSpec::new_port(1).unwrap(),
                PortSpec::new_range(445, 446).unwrap(),
                PortSpec::new_range(567, 678).unwrap()
            ]
        );

        let ep = EndPoint::new("172.16.0.0/16^  UDP ^ 3389").unwrap();
        assert_eq!(ep.is_names_only(), false);
        assert_eq!(ep.is_ipv4_only(), true);
        assert_eq!(
            ep.zones(),
            &vec![Zone::Network(
                Ipv4Network::from_str("172.16.0.0/16").unwrap()
            )]
        );
        assert_eq!(ep.to_string(), "172.16.0.0/16^udp^3389");

        let ep: EndPoint = "172.16.0.0/16,#eth0^tcp,udp^3389,1-65535".parse().unwrap();
        assert_eq!(ep.to_string(), "172.16.0.0/16,#eth0^tcp,udp^3389,1-65535");
        assert_eq!(ep.interfaces(), &vec![NetworkInterface("eth0".into())]);
        assert_eq!(ep.is_names_only(), false);
        assert_eq!(ep.is_ipv4_only(), true);
        assert_eq!(
            ep.zones(),
            &vec![Zone::Network(
                Ipv4Network::from_str("172.16.0.0/16").unwrap()
            )]
        );
        assert_eq!(ep.interfaces(), &vec![NetworkInterface("eth0".to_string())]);
        let ep2: EndPoint = "  172.16.0.0/16   ,     #eth0 ^   tcp,   udp ^  3389,   1-65535     "
            .parse()
            .unwrap();
        assert_eq!(ep, ep2);

        let ep: EndPoint = " security.debian.org. , example.com. ^   tcp"
            .parse()
            .unwrap();
        assert_eq!(ep.to_string(), "security.debian.org.,example.com.^tcp");
        let v: Vec<NetworkInterface> = vec![];
        assert_eq!(ep.interfaces(), &v);
        assert_eq!(ep.is_names_only(), true);
        assert_eq!(ep.is_ipv4_only(), false);
        assert_eq!(
            ep.zones(),
            &vec![
                Zone::Name(Name::new("security.debian.org.").unwrap()),
                Zone::Name(Name::new("example.com.").unwrap())
            ]
        );

        assert!(EndPoint::from_str("*.").is_ok());
        assert!(EndPoint::from_str("*").is_ok());
        assert!(EndPoint::from_str("**.").is_ok());
        assert!(EndPoint::from_str("**").is_err());
        assert!(EndPoint::from_str("a.b**.c.").is_err());
        assert!(EndPoint::from_str("a.b*.c.").is_ok());
    }

    #[test]
    fn endpoint_contains() {
        let ep1 = EndPoint::from_str("**.ntp.org.").unwrap();
        let ep2 = EndPoint::from_str("*.debian.pool.ntp.org.").unwrap();
        assert!(ep1.contains(&ep2));

        let ep1 = EndPoint::from_str("example.com.").unwrap();
        let ep2 = EndPoint::from_str("example.com.").unwrap();
        assert!(ep1.contains(&ep2));

        let ep1 = EndPoint::from_str("*.example.com.").unwrap();
        let ep2 = EndPoint::from_str("example.com.").unwrap();
        assert!(!ep1.contains(&ep2));

        let ep1 = EndPoint::from_str("*example.com.").unwrap();
        let ep2 = EndPoint::from_str("example.com.").unwrap();
        assert!(ep1.contains(&ep2));
        let ep2 = EndPoint::from_str("someexample.com.").unwrap();
        assert!(ep1.contains(&ep2));
        let ep2 = EndPoint::from_str("some.example.com.").unwrap();
        assert!(!ep1.contains(&ep2));

        let ep1 = EndPoint::from_str("**example.com.").unwrap();
        let ep2 = EndPoint::from_str("www.example.com.").unwrap();
        assert!(ep1.contains(&ep2));
        let ep2 = EndPoint::from_str("www.someexample.com.").unwrap();
        assert!(ep1.contains(&ep2));
        let ep2 = EndPoint::from_str("a.www.example.com.").unwrap();
        assert!(ep1.contains(&ep2));

        let ep1 = EndPoint::from_str("*.example.com.").unwrap();
        let ep2 = EndPoint::from_str("*.example.com.").unwrap();
        assert!(ep1.contains(&ep2));

        let ep1 = EndPoint::from_str("**example.com.").unwrap();
        let ep2 = EndPoint::from_str("**example.com.").unwrap();
        assert!(ep1.contains(&ep2));
        let ep2 = EndPoint::from_str("**someexample.com.").unwrap();
        assert!(ep1.contains(&ep2));
        let ep2 = EndPoint::from_str("**ample.com.").unwrap();
        assert!(!ep1.contains(&ep2));
        let ep2 = EndPoint::from_str("*.example.com.").unwrap();
        assert!(ep1.contains(&ep2));
        let ep2 = EndPoint::from_str("*.www.example.com.").unwrap();
        assert!(ep1.contains(&ep2));
        let ep2 = EndPoint::from_str("*.www.someexample.com.").unwrap();
        assert!(ep1.contains(&ep2));

        let ep1 = EndPoint::from_str("**.example.com.").unwrap();
        let ep2 = EndPoint::from_str("*w.example.com.").unwrap();
        assert!(ep1.contains(&ep2));
        let ep2 = EndPoint::from_str("*.example.com.").unwrap();
        assert!(ep1.contains(&ep2));
        let ep2 = EndPoint::from_str("*.web.example2.com.").unwrap();
        assert!(!ep1.contains(&ep2));
        let ep2 = EndPoint::from_str("*.web.example.com.").unwrap();
        assert!(ep1.contains(&ep2));

        let ep1 = EndPoint::from_str("w*.example.com.").unwrap();
        let ep2 = EndPoint::from_str("*example.com.").unwrap();
        assert!(!ep1.contains(&ep2));
        let ep2 = EndPoint::from_str("w*.example.com.").unwrap();
        assert!(ep1.contains(&ep2));
        let ep2 = EndPoint::from_str("www.example.com.").unwrap();
        assert!(ep1.contains(&ep2));
        let ep2 = EndPoint::from_str("w.example.com.").unwrap();
        assert!(ep1.contains(&ep2));
        let ep1 = EndPoint::from_str("**example.com.^tcp^443,80").unwrap();
        let ep2 = EndPoint::from_str("example.com.^tcp^80").unwrap();
        assert!(ep1.contains(&ep2));

        let ep1 = EndPoint::from_str("#eth0^icmp").unwrap();
        let ep2 = EndPoint::from_str("^icmp").unwrap();
        assert!(!ep1.contains(&ep2));
        let ep1 = EndPoint::from_str("^icmp").unwrap();
        let ep2 = EndPoint::from_str("#eth0^icmp").unwrap();
        assert!(ep1.contains(&ep2));
        let ep1 = EndPoint::from_str("#eth0").unwrap();
        let ep2 = EndPoint::from_str("#eth0^icmp").unwrap();
        assert!(ep1.contains(&ep2));
        let ep1 = EndPoint::from_str("#eth0,#eth1").unwrap();
        let ep2 = EndPoint::from_str("#eth0,#eth3").unwrap();
        assert!(!ep1.contains(&ep2));

        let ep1 = EndPoint::from_str("^tcp").unwrap();
        let ep2 = EndPoint::from_str("^tcp,udp").unwrap();
        assert!(!ep1.contains(&ep2));
        assert!(ep2.contains(&ep1));
        let ep1 = EndPoint::from_str("").unwrap();
        let ep2 = EndPoint::from_str("^tcp,udp").unwrap();
        assert!(ep1.contains(&ep2));
        assert!(!ep2.contains(&ep1));

        let ep1 = EndPoint::from_str("").unwrap();
        let ep2 = EndPoint::from_str("^^123,234-256").unwrap();
        assert!(ep1.contains(&ep2));
        assert!(!ep2.contains(&ep1));
        let ep1 = EndPoint::from_str("^^100-300").unwrap();
        assert!(ep1.contains(&ep2));
        assert!(!ep2.contains(&ep1));

        let ep1 = EndPoint::from_str("1.2.0.0/16").unwrap();
        let ep2 = EndPoint::from_str("1.2.3.4").unwrap();
        assert!(ep1.contains(&ep2));
        assert!(!ep2.contains(&ep1));
        let ep2 = EndPoint::from_str("1.2.3.4/32").unwrap();
        assert!(ep1.contains(&ep2));
        assert!(!ep2.contains(&ep1));
        let ep2 = EndPoint::from_str("1.3.3.4/32").unwrap();
        assert!(!ep1.contains(&ep2));

        let ep1 = EndPoint::from_str("1.2.0.0/16^TCP^443,444").unwrap();
        let ep2 = EndPoint::from_str("1.2.3.4^TCP^443").unwrap();
        assert!(ep1.contains(&ep2));
        assert!(!ep2.contains(&ep1));
        let ep2 = EndPoint::from_str("1.2.3.4^TCP^445").unwrap();
        assert!(!ep1.contains(&ep2));

    }
}
