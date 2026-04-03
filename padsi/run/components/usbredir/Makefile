CC = gcc
CFLAGS = -Wall -Wextra

all: helper-proxy usbredir-server

helper-proxy: helper-proxy.c
	$(CC) $(CFLAGS) -o $@ $<
	strip $@

usbredir-server: usbredir-server.c
	$(CC) $(CFLAGS) -o $@ $<
	strip $@

clean:
	rm -f helper-proxy usbredir-server
