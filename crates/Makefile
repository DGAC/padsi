build:
	make -C data-access-guard
	make -C fw-logger
	make -C init
	make -C padsi-do
	make -C usb-monitor
	make -C vm-agent
	make -C wayland-proxy
	make -C web-infra

audit:
	-make -C data-access-guard audit
	-make -C fw-logger audit
	-make -C init audit
	-make -C nflog-rs audit
	-make -C padsi audit
	-make -C padsi-do audit
	-make -C usb-monitor audit
	-make -C vm-agent audit
	-make -C wayland-proxy audit
	-make -C web-infra audit

clean:
	make -C data-access-guard clean
	make -C fw-logger clean
	make -C init clean
	make -C nflog-rs clean
	make -C padsi clean
	make -C padsi-do clean
	make -C usb-monitor clean
	make -C vm-agent clean
	make -C wayland-proxy clean
	make -C web-infra clean
