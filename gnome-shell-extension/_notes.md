## Build. env installation

From a debian:trixie (otherwise 'just' is not available)
~~~
    apt update
    apt install nodejs gettext npm just libglib2.0-bin zip
~~~

## Build the extension

~~~
    just pack
~~~

Also:
- `just --list`
- `just build` -> all in the _build directory
- `just install` -> all in the ~/.local/share/gnome-shell/extensions/rounded-window-corners@fxgn

## Debug

~~~
MUTTER_DEBUG_DUMMY_MONITOR_SCALES=1 MUTTER_DEBUG_DUMMY_MODE_SPECS=1600x1200 dbus-run-session -- gnome-shell --nested --wayland --wayland-display wayland-1
~~~

hint to use bwrap: 
~~~
bwrap --bind / / --dev-bind /dev /dev --clearenv --tmpfs $XDG_RUNTIME_DIR --bind $XDG_RUNTIME_DIR/wayland-0 $XDG_RUNTIME_DIR/wayland-0 --setenv WAYLAND_DISPLAY $WAYLAND_DISPLAY --setenv XDG_RUNTIME_DIR $XDG_RUNTIME_DIR bash

~~~

### To log all levels
create file ~/.config/systemd/user/org.gnome.Shell@wayland.service.d/debug.conf with:
~~~
# make gnome-shell show messages for *all* log levels
[Service]
Environment="G_MESSAGES_DEBUG=all"
~~~


## Misc.

- use the `gnome-extensions` GNOME tool to manage extensions
- to install an extension:
    - for the current user (in `$HOME/.share/gnome-shell/extensions`): `gnome-extensions install --force <ext>.zip`
    - for all users (system wide):
        - create a `/usr/share/gnome-shell/extensions/<ext UID>` directory
        - extract the ZIP archive in that directory


## References
- Makefile: https://github.com/win0err/gnome-runcat/blob/master/Makefile
- https://gjs.guide/extensions/topics/extension.html and https://gjs.guide/extensions/topics/extension.html#injectionmanager
