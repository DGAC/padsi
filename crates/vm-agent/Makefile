all: build-linux build-windows

build-linux:
	cargo build --release
	strip target/release/vm-agent

build-windows:
	cargo build --target x86_64-pc-windows-gnu --release

clean:
	cargo clean
