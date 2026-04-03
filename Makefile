all: compile extension

compile:
	make -C padsi/run/mutter-appid
	make -C padsi/run/netlink-shim
	make -C crates

extension:
	make -C gnome-shell-extension dist

clean:
	make -C padsi/run/mutter-appid clean
	make -C padsi/run/netlink-shim clean
	make -C crates clean
	make -C gnome-shell-extension clean

dist: all
	@./dist.sh
