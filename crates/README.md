# PADSI rust code

Crates for programs and libraries used in PADSI

- `data-access-guard`: block access to some files beyond the traditional access rights
- `fw-logger`: firewall logger which relies on NFLog events
- `init`: "init" process to be run in the "bubbles"
- `padsi`: misc. functions (tracing, etc)
- `padsi-do`: program to execute programs in the context of admin. namespaces
- `usb-monitor`: monitor the usage of USB devices
- `vm-agent`: PADSI agent to be run in VMs
- `wayland-proxy`: Wayland proxy to filter copy/paste
- `web-infra`: filtering Web proxy, WPAD server and Web redirection

- `nflog-rs`:           crate imported AS-IS from https://github.com/chifflier/nflog-rs (required by fw-logger)
