# PADSI

PADSI is a security software package designed for Linux desktop users which allows them to run programs within constrained environments regardings access to files and network resources. It is in a way similar to what [Qubes OS](https://www.qubes-os.org/) achieves but using Linux's kernel isolation mechanisms instead of virtual machines
(and thus inherently less secure), in a way it can be seen as using containers on a desktop setup.

The key to desing of those environments, which are called **zones**, is to map them to the users' various activities and their relative security needs in an exclusive mode. For instance, for a personal usage, one might define a "private" zone in which all personal sensitive work is being done (such as banking, or e-mailing) and an "internet" zone in which the user surfs the global Internet. Applications running in the first zone have access to personal files and a limited set of network resources (like the bank's web site and the personal e-mail services) whereas in the second zone, no personel files is accessible but all the Web sites on the Internet are (with the exception of bank's web site and the personal e-mail services).



## Features

- end-users can easily run applications in zones with a visual feedback (a colored outline around each window and icon) to identify which application is running in which zone;
- zones need not be completely isolated (even thoug they are by deault), they can share some directories (in read-only or read-write mode);
- end-users and administrators can easily see which network access has been blocked (and sometimes which network access are actually used);
- system administrators can force the network traffic of some zones to go through a VPN (WireGuard and OpenVPN arre currently supported), refered to as **traffic shapers** (anticipating future features related to network handling);
- programs running outside of any zone, sometimes refered to as the "**init zone**" are constrained by the netfilter firewall to some white listed network resources. For example incoming SSH connections might be granted to allow remote SSH access to the system.
- If VPNs are used, some incoming and outgoing network traffic can also be allowed via a VPN using **administration namespaces**;
- end-users can run and manage virtual machines to achieve specific tasks:
  - system administrators define the set of virtual machine templates which can be used and are responsible for the configuration and maintenance of these templates;
  - each virtual machine run by a user is customized for the end user, specifically (but depending on how the virtual machine template is set up), the user has a local account in the virtual machine which is the same as the one in the host system;
  - each virtual machine is running in the context of a zone and is at least constrained by that zone (administrators can add more contraints if needed), and can (depending on their definition) share directories in the user's home on the host system; 
  - users can simultaneously run more than one virtual machine basd on the same template (of course withing the hardware's capabilities) to create a local network of systems (and for example SSH into each if the SSH service is enabled on them);
  - users are responsible to manage their own virtual machines in a way similar to how one managed Docker or Podman containers.
- Web traffic can be routed through external Web proxies;
- access to known malicious or ads distribution web sites can be blocked on a per zone basis.

### Limitations
- PADSI is primarily designed to work in a desktop environment using Wayland, and lets X11 clients being run if specified; 
- PADSI only supports using the GNOME desktop environment as no test with any other environment has been done, and as the colored outline around the windows will need to be adapted for each desktop environment;
- end-users can't use container technologies like Docker or Podman or virtual machines via libvirt as the services providing these features are running in the "init zone";
- PADSI has currently been only tested on a Debian 13 system, but it should work with minimal adaptations on other (recent) Linux distributions;
- PADSI does not yet support any internationalization.




## How it's done

Here a very consise summary of how PADSI is implemented:
- The isolation features are implemented as a global systemd service which instantiates a per-user process for each logged-in user;
- when a user first start a program in a specific zone, PADSI creates the following "bubbles" (in reference to the [Bubblewrap](https://github.com/containers/bubblewrap) tool which does all the work):
  - one for the dedicated infrastructure in support for the applications running in the zone (DNS, web proxy, firewall logging, Wayland proxy, etc.)
  - one for the end user's applications.
- each zone configuration has its own HOME directory (located in `<padsi's root path>/home/<UID>/<zone name>`) but can also mount directories from the HOME directory of the user in the host (user's files), or system wide directories;
- the colored outline around each window of each application is performed via a GNOME shell extension;
- virtual machines are run using [QEMU](https://www.qemu.org/) (libvirt is not used);
- many other ancillary programs ensure a smooth integration with the GNOME desktop and provide some extra security;
- PADSI is mainly written in Python and Rust.



## Getting started


### Installation
For a quick test, using a Debian 13 Linux system:
1. download an install the padsi package or compile it;
2. create the `/etc/padsi` directory and copy in it the example configuration files from `/usr/share/doc/padsi/conf-example/`;
3. start the padsi service (`systemctl start padsi`).

Adjust the configuration according to the needs, and restart the padsi service each time it is modified (PADSI can't simply reload the configuration). Keep in mind that restarting the padsi service will terminate all the programs running in all the zones (save any unsaved data first)



### Configuration
In order for PADSI to provide the best security, absolutly respect the following rule for any defined zone: **either allow an almost unconstrained access to the Internet OR (in an exclusive way) grant access to files and network resources which have any kind of value, DON'T mix the two**. This rule protects against lateral movement, credential theft and most ransomwares (it does not though protect against wipers). The reasoning behind this very simple rule is that any malicious piece of software which might be running in a zone MUST NEVER be able to simultaneously communicate with the threat actor's Command and Control infrastructure (including via DNS resolution) and have access to files or network resources which have any sort of value.

Another rule which should be taken into account is: **don't allow access to any authenticated service in a zone if this zone also allows an almost uncontrolled access to the Internet**. This rule protects againts phishing when the user is informed that he/she should NEVER provide any knind of credential in the zone which allows an almost uncontrolled access to the Internet.

Finally, in order to protect against data exfiltration and avoid any communications with a threat actor's Command and Control infrastructure, a 3rd rule can be to **prevent uncontrolled DNS resolution**. In the context of unrestricted Internet access, one can rely on external web proxies (which perform DNS resolution themselves) and still prevent any direct DNS resolution.

The configuration is a set of JSON files located in the `/etc/padsi` directory by default, refer to the documentation for all the available settings. Basically:
- the `padsi.conf` file holds the global configuration:
- network resources (grouping network resources by use case) are defined in `.netres` files;
- zones' configurations are defined in `.zone` files;
- Virtual machines configurations  are defined in `.vm` files;
- traffic shapers are deffined in `.tsp` files;
- administration namespaces are defined in `.admns` files.
