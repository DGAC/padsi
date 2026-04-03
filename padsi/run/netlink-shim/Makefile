CC = gcc
CFLAGS = -Wall -Wextra
LDFLAGS = -O3 -shared -fPIC -static-libgcc

all: netlink-proxy netlink-shim.so

netlink-proxy: netlink-proxy.c
	$(CC) $(CFLAGS) -o $@ $<
	strip $@

netlink-shim.so: netlink-shim.c
	$(CC) $(CFLAGS) $(LDFLAGS) -DSONAME="$@" -o $@ $^
	strip $@

clean:
	rm -f netlink-proxy netlink-shim.so
