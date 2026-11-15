#include <stdint.h>
#include "platform.h"
#include "../common/test_status.h"

/*
 * Paired with tb/cocotb/test_sw_bus_reset.py. The pull-up is the handshake:
 * the host drives a bus reset only after it rises.
 *   1. device_address is written and reads back; no bus event is pending.
 *   2. After the host's bus reset, bus_event.bus_reset is set and hardware
 *      has cleared device_address.
 *   3. Writing 1 clears bus_event.bus_reset.
 */

#define DEVICE_ADDRESS 5u
#define TIMEOUT 200000u

static void expect(int condition, uint32_t status) {
    if (!condition) {
        test_finish(status);
    }
}

int main(void) {
    while (USB_DEVICE->status.f.vbus_present == 0) { }

    USB_DEVICE->control.f.enable_core = 1;
    USB_DEVICE->bus_event.w = USB_DEVICE__BUS_EVENT__BUS_RESET_bm;
    expect((USB_DEVICE->bus_event.w & USB_DEVICE__BUS_EVENT__BUS_RESET_bm) == 0, 2);

    USB_DEVICE->device_address.w = DEVICE_ADDRESS;
    expect(USB_DEVICE->device_address.f.address == DEVICE_ADDRESS, 3);

    USB_DEVICE->control.f.enable_pu = 1;

    uint32_t i = 0;
    while ((USB_DEVICE->bus_event.w & USB_DEVICE__BUS_EVENT__BUS_RESET_bm) == 0) {
        expect(++i < TIMEOUT, 10);
    }
    expect(USB_DEVICE->device_address.f.address == 0, 11);

    USB_DEVICE->bus_event.w = USB_DEVICE__BUS_EVENT__BUS_RESET_bm;
    expect((USB_DEVICE->bus_event.w & USB_DEVICE__BUS_EVENT__BUS_RESET_bm) == 0, 12);

    test_finish(1);
    return 0;
}
