#!/bin/bash

#
# Copyright (c) 2025-2026 DGAC/DSNA
#
# This file is part of PADSI.
#
# This software is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This software is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this software.  If not, see <http://www.gnu.org/licenses/>.
#

VERSION=0.9.29

set -e

srcdir=$(dirname $(realpath "$0"))
echo "Padsi src directory: $srcdir"
echo "Version: $VERSION"
vm_mgmt_srcdir="$srcdir/vm-management"
out_dir="$srcdir/packages"
mkdir -p "$out_dir"

function create_main_package() {
    local vm_deb_pkg="$1"
    local vm_lx_pkg="$2"
    local vm_win_pkg="$3"
    tmpdir=$(mktemp -d)

    # prepare diretory structure
    bindir="$tmpdir/usr/bin"
    etcdir="$tmpdir/etc"
    installdir="$tmpdir/usr/share/padsi"
    systemddir="$tmpdir/usr/lib/systemd/system"
    docdir="$tmpdir/usr/share/doc/padsi"
    pbindir="$installdir/bin"

    mkdir -p "$bindir"
    mkdir -p "$etcdir"
    mkdir -p "$installdir"
    mkdir -p "$pbindir"
    mkdir -p "$systemddir"
    mkdir -p "$docdir"

    # copy DEBIAN files
    mkdir -p "$tmpdir/DEBIAN"
    cat "$srcdir/DEBIAN/main-control" | sed -e "s/@VERSION@/$VERSION/g" > "$tmpdir/DEBIAN/control"
    cp "$srcdir/DEBIAN/main-postinst" "$tmpdir/DEBIAN/postinst"
    cp "$srcdir/DEBIAN/main-prerm" "$tmpdir/DEBIAN/prerm"
    cp "$srcdir/DEBIAN/main-postrm" "$tmpdir/DEBIAN/postrm"

    # doc
    cp "$srcdir/DEBIAN/copyright" "$docdir"
    cp "$srcdir/Changelog.md" "$docdir/changelog.md"
    gzip -n -9 "$docdir/changelog.md"
    mkdir "$docdir/conf-example"
    cp "$srcdir/etc/padsi/"* "$docdir/conf-example"

    # profile configuration
    mkdir -p "$installdir/etc/profile.d"
    cp "$srcdir/etc/profile.d/zzz_padsi.sh.templ" "$installdir/etc/profile.d"

    # dconf extension enable
    cp -aR "$srcdir/etc/dconf" "$etcdir/dconf"

    # PADSI files
    cp "$srcdir/systemd/padsi.service" "$systemddir/"
    cp "$srcdir/systemd/padsi-system-service" "$pbindir"
    cp "$srcdir/padsi/run/user-service" "$pbindir"
    cp "$srcdir/padsi/run/admin-service" "$pbindir"
    cp "$srcdir/padsi/run/monitor-desktop-entries" "$pbindir"
    cp "$srcdir/crates/usb-monitor/target/release/usb-monitor" "$pbindir"
    cp "$srcdir/crates/padsi-do/target/release/padsi-do" "$pbindir"
    ln -s "../share/padsi/bin/padsi-do" "$bindir/padsi-do"
    cp "$srcdir/crates/data-access-guard/target/release/data-access-guard" "$pbindir"

    mkdir -p "$installdir/padsi"
    mkdir -p "$installdir/nsbubble"
    rsync -avd --exclude=__pycache__ --exclude dbus-router --exclude mutter-appid --exclude netlink-shim --exclude *.pkp \
          --exclude user-service --exclude monitor-desktop-entries \
          "$srcdir/padsi/"* "$installdir/padsi/" > /dev/null
    ln -s "../share/padsi/padsi/cli/padsi-cli-host" "$bindir/padsi-cli"
    ln -s "../share/padsi/padsi/cli/padsi-sys" "$bindir/padsi-sys"
    rsync -avdl --exclude=__pycache__ "$srcdir/nsbubble/"* "$installdir/nsbubble/" > /dev/null
    rsync -avdl --exclude=__pycache__ "$srcdir/firewall/"* "$installdir/firewall/" > /dev/null
    rsync -avdl --exclude=__pycache__ --exclude=padsi-agent "$srcdir/vm-management/"* "$installdir/vm-management/" > /dev/null

    src_compdir="$srcdir/padsi/run/components"
    install_compdir="$installdir/padsi/run/components"

    cp "$srcdir/crates/init/target/release/init" "$installdir/nsbubble"
    rm "$installdir/nsbubble/crate"

    rm "$install_compdir/fw_logger/crate"
    cp "$srcdir/crates/fw-logger/target/release/fw-logger" "$install_compdir/fw_logger"
    ln -s "../padsi/run/components/fw_logger/fw-logger" "$pbindir"

    rm "$install_compdir/web_infra/crate"
    src_webroot=$(realpath "$src_compdir/web_infra/crate/webroot")
    rsync -avdl "$src_webroot" "$install_compdir/web_infra" > /dev/null
    cp "$srcdir/crates/web-infra/target/release/web-infra" "$install_compdir/web_infra"

    rm "$install_compdir/wayland_proxy/crate"
    cp "$srcdir/crates/wayland-proxy/target/release/wayland-proxy" "$install_compdir/wayland_proxy"

    # mutter's preloader
    cp "$srcdir/padsi/run/mutter-appid/mutter-appid.so" "$pbindir"
    mkdir -p "$tmpdir/etc/systemd/user/org.gnome.Shell@wayland.service.d"
    cp "$srcdir/padsi/run/mutter-appid/org.gnome.Shell@wayland.service-override.conf" "$tmpdir/etc/systemd/user/org.gnome.Shell@wayland.service.d/padsi.conf"

    # netlink shim
    cp "$srcdir/padsi/run/netlink-shim/netlink-shim.so" "$pbindir"
    cp "$srcdir/padsi/run/netlink-shim/netlink-proxy" "$pbindir"

    # Gnome shell extension
    extdir="$installdir/gnome-shell-extensions"
    mkdir -p "$extdir"
    extname="rounded-window-corners"
    zipfile="$srcdir/gnome-shell-extension/$extname/padsi@dgac.shell-extension.zip"
    [ -f "$zipfile" ] || {
        echo "The Gnome Shell extension in '$srcdir/gnome-shell-extension/$extname' needs to be compiled"
        exit 1
    }
    cp "$zipfile" "$extdir/padsi@dgac.shell-extension.zip"

    # DBus router
    prog_bin="$srcdir/padsi/run/dbus-router/dbus-router"
    cp "$prog_bin" "$pbindir/dbus-router"
    lib_dir="$srcdir/padsi/run/dbus-router/dbus_min"
    mkdir -p "$installdir/dbus_min"
    cp "$lib_dir"/*.py "$installdir/dbus_min/"

    # helpers
    mkdir -p "$installdir/helpers"
    for file in "generate-blocklist" "generate-iso-file" "padsi-ms365-importer"; do
        cp "$srcdir/helpers/$file" "$installdir/helpers/"
    done

    # agent packages
    pkg_dir="$installdir/packages"
    mkdir "$pkg_dir"
    cp "$vm_deb_pkg" "$vm_lx_pkg" "$vm_win_pkg" "$pkg_dir"

    # final DEB file
    debfile=$(mktemp)
    dpkg-deb --root-owner-group --build "$tmpdir" "$debfile" >/dev/null 2>&1
    mv "$debfile" "$out_dir/.deb"
    nameres=$(LANG=C dpkg-name -o "$out_dir/.deb")
    chmod 644 "$out_dir/"*.deb
    outfile=$(echo "$nameres" | sed -e "s/^.*to '//" -e "s/'//")

    # cleanups
    rm -rf "$tmpdir"
    echo "$outfile"
}

function build_padsi_vm_agent_package_debian() {
    tmpdir=$(mktemp -d)
    crate_dir="$vm_mgmt_srcdir/vm-agent"

    # prepare diretory structure
    bindir="$tmpdir/usr/bin"
    systemddir="$tmpdir/usr/lib/systemd/system"
    docdir="$tmpdir/usr/share/doc/padsi"

    mkdir -p "$bindir"
    mkdir -p "$systemddir"
    mkdir -p "$docdir"

    # copy DEBIAN files
    mkdir -p "$tmpdir/DEBIAN"
    cat "$srcdir/DEBIAN/vm-agent-control" | sed -e "s/@VERSION@/$VERSION/g" > "$tmpdir/DEBIAN/control"
    cp "$srcdir/DEBIAN/vm-agent-postinst" "$tmpdir/DEBIAN/postinst"

    # doc
    cp "$srcdir/DEBIAN/copyright" "$docdir"
    cp "$srcdir/Changelog.md" "$docdir/changelog.md"
    gzip -n -9 "$docdir/changelog.md"

    # copy PADSI files
    cp "$crate_dir/resources/padsi-agent.service" "$systemddir"
    cp "$crate_dir/target/release/vm-agent" "$bindir/padsi-agent"

    # build the DEB file
    debfile=$(mktemp)
    dpkg-deb --root-owner-group --build "$tmpdir" "$debfile" >/dev/null 2>&1
    mv "$debfile" "$out_dir/.deb"
    nameres=$(LANG=C dpkg-name -o "$out_dir/.deb")
    chmod 644 "$out_dir/"*.deb
    outfile=$(echo "$nameres" | sed -e "s/^.*to '//" -e "s/'//")

    # cleanups
    rm -rf "$tmpdir"
    echo "$outfile"
}

function build_padsi_vm_agent_package_linux() {
    tmpdir=$(mktemp -d)
    crate_dir="$vm_mgmt_srcdir/vm-agent"

    # prepare diretory structure
    bindir="$tmpdir/usr/bin"
    systemddir="$tmpdir/usr/lib/systemd/system"
    docdir="$tmpdir/usr/share/doc/padsi"

    mkdir -p "$bindir"
    mkdir -p "$systemddir"
    mkdir -p "$docdir"

    # doc
    cp "$srcdir/Changelog.md" "$docdir/changelog.md"
    gzip -n -9 "$docdir/changelog.md"

    # copy PADSI files
    cp "$crate_dir/resources/padsi-agent.service" "$systemddir"
    cp "$crate_dir/target/release/vm-agent" "$bindir/padsi-agent"

    # build the archive
    pushd "$tmpdir" >/dev/null
    outfile="$out_dir/padsi-vm-agent_${VERSION}_linux.tar.gz"
    tar czf "$outfile" * >/dev/null 2>&1
    popd >/dev/null

    # cleanups
    rm -rf "$tmpdir"
    echo "$outfile"
}

function build_padsi_vm_agent_package_windows() {
    tmpdir=$(mktemp -d)

    crate_dir="$vm_mgmt_srcdir/vm-agent"
    for file in install.ps1 user-session-opened.ps1
    do
        cp "$crate_dir/resources/$file" "$tmpdir/"
    done
    cp "$crate_dir/target/x86_64-pc-windows-gnu/release/vm-agent.exe" "$tmpdir/padsi-agent.exe"

    pushd "$tmpdir" >/dev/null
    outfile="$out_dir/padsi-vm-agent_${VERSION}_windows.zip"
    rm -f "$outfile" # in case it was present, we don't want to add files to the existing archive
    zip "$outfile" * >/dev/null 2>&1
    popd >/dev/null

    # cleanups
    rm -rf "$tmpdir"
    echo "$outfile"
}

function build_vm_ISO() {
    tmpdir=$(mktemp -d)

    for file in $@
    do
        cp "$file" "$tmpdir/"

        # extract ZIP and targz archives in the ISO
        ext="${file##*.}"
        prefix="${file%.*}"
        case "$ext" in
            zip)
                prefix=$(basename "$prefix")
                mkdir "$tmpdir/$prefix"
                unzip "$file" -d "$tmpdir/$prefix" >/dev/null
                ;;
            gz)
                ext2="${prefix##*.}"
                [ "$ext2" == "tar" ] && {
                    prefix2="${prefix%.*}"
                    prefix=$(basename "$prefix2")
                    mkdir "$tmpdir/$prefix"
                    tar xzf "$file" -C "$tmpdir/$prefix"
                }
                ;;
        esac
    done

    outfile="$out_dir/padsi-vm-agent_${VERSION}.iso"
    mkisofs -V "PADSI-VM-agent-${VERSION}" -r -o "$outfile" -quiet "$tmpdir"

    # cleanups
    rm -rf "$tmpdir"
    echo "$outfile"
}

command -v dpkg-name >/dev/null || {
    echo "The dpkg-name command is missing (dpkg-dev package)"
    exit 1
}

echo -n "building Debian VM padsi-agent package "
vm_deb_pkg=$(build_padsi_vm_agent_package_debian)
echo "=> $vm_deb_pkg"

echo -n "building generic Linux padsi-agent package "
vm_lx_pkg=$(build_padsi_vm_agent_package_linux)
echo "=> $vm_lx_pkg"

echo -n "building Windows VM padsi-agent package "
vm_win_pkg=$(build_padsi_vm_agent_package_windows)
echo "=> $vm_win_pkg"

echo -n "building VM install ISO "
vm_iso=$(build_vm_ISO "$vm_deb_pkg" "$vm_lx_pkg" "$vm_win_pkg")
echo "=> $vm_iso"

echo -n "building main Debian padsi package "
pkg=$(create_main_package "$vm_deb_pkg" "$vm_lx_pkg" "$vm_win_pkg")
echo "=> $pkg"
