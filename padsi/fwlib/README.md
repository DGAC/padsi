# Firewall wrapper in Python

This directory contains a module which deals with simple netfilter firewall manipulations.

It is not intended to cover all the netfilter possible configurations but rather to perform
some basic configuration in the context of a network namespace where there is no already
configured rules.

This module relies on the 'nft' tool and does not support using the legacy `iptables` tool.

## Notions
Endpoints can be used to:
- to perform network filtering (inbound or outbound), if used alone; or
- in a flow to manage end to end network communications when 2 endpoints are used

NB:
- only 1 interface can be specified in zones (not yet in Rust, TBC)
- '*' means ANY value, and is similar to '' but more intelligible

Endpoint matching rules: an endpoint A contains and endpoint B (i.e. B matches A) if and only if:
- all B's zones match a zone in A
- for interfaces, ports and protocols items:
    - if B specifies any item: all of B's interfaces match an interface in A OR A does not specify any interface
    - otherwise: A does not specify any item


## Grammar

~~~
flow =      <endpoint> '>' [ <protocols> ] '>' <endpoint>

endpoint =  <zones> [ <interfaces> ] [ '^' <protocols> [ '^' <ports> ] ]

zones =     ['*' | '' ] | <zone> [ ','  <zone> ... ]

zone =      <cidrv4> | <ipv4> | <dname> | <dpattern>

interfaces =<interface> [ <interfaces> ...]                                  # named network interface
interface = '#' <interface>


protocols = <proto> [ ',' <proto> ...]
proto =     'tcp' | 'udp' | 'icmp'

ports =     <port> | <portrange> [ ',' <ports> | <portrange> ... ]
portrange = <port> '-' <port>
port =      1 - 65535

ipv4 =      a valid IPv4, see validate_ipv4_format()
cidrv4 =    a valid CIDR v4, see validate_cidrv4_format()
dname =     a valid domain name (with the final '.' characted), see is_domain_name()
dpattern =  a valid domain name pattern: a dname where:
            - some parts may contain a '*' meaning any number of characters except the '.'
            - may start with the '**' string meaning any number of characters
interface=  a network interface, validate_network_interface_format()
~~~
