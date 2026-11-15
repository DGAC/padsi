# Getting started

## Installation

### Default example configuration

For a quick test, using a Debian 13 Linux system, in the GNOME environment (other graphical environments are not _yet_ supported), the default configuration is probably enough, with the following zones defined:

- `dev`: a zone for development activities set up to allow access to some repositories and other resources usefull to developpers; DNS in this zone is performed using 1.1.1.1 and 9.9.9.9 and DNS over TLS;
- `restricted`: a zone to access Web sites where users are authenticated (to avoid credentials theft) set up with a very short lis of banking and social media sites; DNS in this zone is performed using DNS4EU services and DNS over TLS;
- `internet`: a zone to access Internet sites except the ones in the `restricted` zone; DNS in this zone is also performed using DNS4EU services and DNS over TLS.

Note that to keep the configuration simple, this configuration sets the system's firewall to the following configuration:

- all incoming traffic is dropped except SSH (on port 22)
- outgoing traffic allow to query any DNS service (on port 53) and any Web traffic

This set up still allows one to use Web resources (from a Web browser or via tools like APT), and is enough to run some tests but **for a more secure configuration, any outgoing traffic should be denied and a specific administration zone should be set up (refer to the documentation)**.

### Installation

Follow those steps to install PADSI:

1. download and install the padsi package or compile it;
2. create the `/etc/padsi` directory and copy the example configuration files from `/usr/share/doc/padsi/conf-example/` in it;
3. start the PADSI service using `systemctl start padsi` (the service is still not enabled and will not be started at the next reboot) ;
4. if a GNOME session was opened, it needs to be closed and re-opened.

Note: most of the GNOME desktop launcher icons will have been changed when the PADSI service is started.

### Simple usage

To interact with PADSI, command line tools must be executed outside of any zone (directly in the "init" namespace), to open such a terminal:

- press ALT-F2 and enter `gnome-terminal`, or
- click on the "Open a terminal" after having clicked on the shield icon in the top right bar of the GNOME desktop.

PADSI's main command line tools are:

- `padsi-cli` for general usage (like running programs in zones);
- `padsi-sys` when developping and/or debugging

For example, to start applications in some zones, run:

```bash
$ padsi-cli run restricted firefox
$ padsi-cli run internet firefox
$ padsi-cli run dev gnome-terminal
```

Adjust the configuration according to your needs, and restart the PADSI service each time it is modified (PADSI can't simply reload the configuration). Keep in mind that restarting the padsi service will terminate any program running in a zone (save any unsaved data first)

### Tips

- for security matters, programs running in a zone can't get any information about the zone they are running in; however, the small number of processes or files in `/etc` will reveal that a program is indeed running in a zone.
- the following program can be manually executed in a terminal in a zone to change the prompt in the zone (the name of the zone must be provided on the command line), adapt it to your needs:
  ```bash
  #/bin/bash
  # Append code at the end of the $HOME/.bashrc to add the current zone in the prompt (takes effect next time a shell is opened)
  [ $# == 1 ] || {
      echo "usage: $0 <zone name>"
      exit 1
  }
  cat << EOF >> ~/.bashrc
  ZONE=$1
  PS1='\[\033[01;35m\]\D{%d%h%H:%M}\[\033[00m\] \[\033[01;32m\][\$ZONE]\[\033[00m\]:\[\033[01;34m\]\w\[\033[00m\] \$ '
  EOF
  ```

## Uninstall and cleanups

To uninstall:

1. stop the PADSI service
2. remove the `/etc/padsi` configuration directory
3. remove the `/var/padsi` directory and its contents
