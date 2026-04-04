# Developing PADSI

## Soure code structure

PADSI is mainly written in Python and Rust.

The structure of the source code is as follows:
- the `crates/` directory contains all Rust code;
- the `doc/` directory contains all the documentation (written using the [Lyx](https://www.lyx.org/) tool);
- the `etc/` directory contains PADSI's example configuration and some other confiruration files packaged with PADSI;
- the `firewall/` directory contains a wrapper around the `nft` tool to manage the netfilter firewall;
- `gnome-shell-extention/` contains the extension to the GNOME shell to outline the various windows with a shadow which color indicates in which zone the associated program is executed
- the `helpers/` directory contains some scripts which can be used to help create PADSI's configuration;
- the `nsbubble/` directory contains a wrapper around the bubblewrap program;
- the `padsi/` directory contains the main part of PADSI's implementation;
- the `systemd/` directory contains contains the files used with systemd;
- the `testing/` directory contains some rudimentary tests;
- the `vm-management/` directory contains the code which need to be installed in the virtual machine's templates for a smooth UX with virtual machines.

Many directories also contain a `README.md` file describing the content of the directory.


## Building a package

For now, packages building is only supported on the Debian 13 distribution.

### Requirements

- for the basic scripts: `sudo apt install make gcc dpkg-dev rsync`
- for the GNOME Shell extension:
  - using the system's NPM installation: `sudo apt install nodejs gettext npm just libglib2.0-bin zip`
  - using a Podman image (the NPM installation will be in the Podman image): `sudo apt install podman`
- Rust (refer to [Rust's install documentation](https://rust-lang.org/tools/install/)):
  - `curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh` 
  - follow the installation to set the environment to use the newly installed programs (cargo, etc)
- eBPF's Rust bpf-linker tool: `cargo install bpf-linker`
- the 'nightly-x86_64-unknown-linux-gnu' toolchain:
  - `rustup toolchain install nightly-x86_64-unknown-linux-gnu`
  - `rustup component add rust-src --toolchain nightly-x86_64-unknown-linux-gnu`
- the x86_64-pc-windows-gnu rust target (to cross compile the Windows VM agent) `rustup target add x86_64-pc-windows-gnu`


### Compilation

Compilation might take a while.

Follow these steps:
1. if not yet done, download PADSI's source code (including the Git submodules): `git clone --recurse-submodules https://github.com/DGAC/padsi`
2. cd into PADSI's source code and:
  - if NPM and the associated tools to compile the extension are present in the system, run `make`
  - if not, then:
    1. run `make -C gnome-shell-extension/latest podman-image`
    2. run `make`
