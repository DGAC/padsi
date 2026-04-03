# DNS resolver component

DNS resolver and manager of the firewall in the bubble according to DNS resolutions.

Required packages:
    - python3-pyinotify

## Resolved zones

In a file which is mapped in the bubble as `/etc/resolv-rules.json`:
- the first rule which match contains the final decision, the default is **deny**
- syntax: an FQDN (ends with a dot)
- the firewall rules may further be restricted to some combination of protocols and ports
