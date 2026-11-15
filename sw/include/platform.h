#ifndef PLATFORM_H
#define PLATFORM_H

#include <stddef.h>
#include <stdint.h>
#include "usb_device_regs.h"

/* Keep in sync with the XBUS address decoder in tb/vhdl/usb_top_tb.vhd. */
#define USB_DEVICE_BASE UINT32_C(0xF0000000)
#define USB_DEVICE ((volatile usb_device_t *)(uintptr_t)USB_DEVICE_BASE)
#define USB_RAM_BASE (USB_DEVICE_BASE + offsetof(usb_device_t, usb_ram))

#endif
