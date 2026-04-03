# Mutter patch

The program in this directory intercepts several function calls of the Mutter GNOME compositor to change the AppID of applications depending on the zone in which they are running from information present in the `/run/padsi/zones-infos/prefixes.txt` file.

The aim here is to ensure that Mutter use the correct application icons from a UX perspective. In the future this "live patch" should be replaced by a native feature of Mutter as is already the case in similar contexts like Flatpak applications.
