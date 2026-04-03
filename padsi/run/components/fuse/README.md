# FUSE component

This component ensures the FUSE filesystem can be used from within a Bubblewrap environment.

The FUSE library calls /usr/bin/fusermount which is (at least in a Debian system) setuid root,
but when Bubblewrap starts, il calls `prctl(PR_SET_NO_NEW_PRIVS)` which disables any privilege elevation
(refer to https://github.com/containers/bubblewrap/issues/378). The result is that FUSE does not
work properly.

This components:
- set up a 'mount-server' process which listens for connections on a Unix socket and runs either
  'fusermount' or 'umount' depending on the request (as normal user)
- replaces the fusermount program with a script which uses that 'mount-server' to actually call the real
  fusermount program
- replaces the 'umount' program with a script also using the 'mount-server' to actually call the real
  umount program
