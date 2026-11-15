# USB device library

Software reference of the portable USB 2.0 full-speed device stack in `sw/usb/` and its NEORV32 port.

The hardware, the register map and how the layers fit together are described in the
[data sheet](../index.html) (the HTML data sheet built next to this reference by `make -C docs all`).

| Layer | Header | Role |
|-------|--------|------|
| L1 registers | usb_ll.h, usb_ram.h | register accessors, shared-RAM copies |
| L2 HAL | usb_hal.h | endpoint entries and buffers, transfers, events |
| L3 device | usbd.h | device state, EP0 control transfers, standard requests, descriptors |
| L4 classes | usbd_class.h, usbd_cdc_acm.h | class driver interface, CDC-ACM |
| Port | usb_port.h, usb_port_neorv32.h | interrupt masking and critical sections |
| Configuration | usb_config_default.h | compile-time options |
