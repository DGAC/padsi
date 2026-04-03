# GNOME Shell extension

The PADSI GNOME shell extension builds on top of the https://github.com/flexagoon/rounded-window-corners
extension to add visual hints (border and drop shadow colors) to the windows depending on their "zone".

The colors of the rounded corners depend on the MNT and NET namespaces of the process associated with the window,
as defined in the `/run/padsi/zones-infos/colors.json`.

ex.:
~~~
{
    "mnt:[4026531841]net:[4026531840]": [1.0, 0.2, 0.2],
    "mnt:[4026531842]net:[4026531840]": [0.1, 1.0, 0.2]
}
~~~


## Compilation

PADSI's extension is a patch applied to the TypeScript source of the above mentionned extension which is in the `latest/` directory.
Compilation can either rely on NPM and other build tools being installed in the system, or can use those very same tools in a Podman image (to avoid otherwise unused tools to be installed in the system).

To use the Podman image, it first needs to be built using `make podman_image`. The Makefile script will determine if the Podman tool and image are present and will adapt accordingly.

Use `make` to:
- build: build the extension (in the _build directory)
- pack: build and pack the extension
- clean: cleanups
- podman_image: create the required podman image
- update: pull any original extension and rebase the modifications on that version
