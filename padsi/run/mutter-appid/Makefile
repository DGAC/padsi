CC = gcc
CFLAGS = -Wall -Wextra
LDFLAGS = -O3 -shared -fPIC -static-libgcc

all: mutter-appid.so

mutter-appid.o: mutter-appid.c utils.h utils.c
	$(CC) $(CFLAGS) -fPIC -c mutter-appid.c

mutter-appid.so: mutter-appid.o
	$(CC) $(CFLAGS) $(LDFLAGS) -DSONAME="$@" -o $@ $^
	strip $@

testing: utils.h utils.c testing.c
	$(CC) $(CFLAGS) -g -o $@ testing.c

clean:
	rm -f mutter-appid.so
	rm -f *.o
	rm -f testing