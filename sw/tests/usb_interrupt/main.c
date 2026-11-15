#include <stdint.h>
#include <neorv32.h>
#include "platform.h"
#include "../common/test_status.h"

/*
 * Paired with tb/cocotb/test_sw_usb_interrupt.py. usb_top's irq is the CPU's
 * machine external interrupt; the firmware reads its pending bit (mip.MEIP)
 * without taking the trap. Each phase ends with a pull-up toggle.
 *   1. interrupt_enable masks everything: after a bus reset, interrupt shows the
 *      pending bus event, but irq stays low and MEIP clear.
 *   2. bus_event enabled: a bus reset raises MEIP; clearing bus_event drops it.
 *   3. endpoint 0 enabled: a SETUP raises MEIP; clearing endpoint[0].interrupt
 *      drops it.
 */

#define TIMEOUT 400000u
#define BUS_RESET USB_DEVICE__BUS_EVENT__BUS_RESET_bm
#define SETUP USB_DEVICE__ENDPOINTX__INTERRUPT__SETUP_bm

static void expect(int condition, uint32_t status) {
    if (!condition) {
        test_finish(status);
    }
}

static int meip(void) {
    return (neorv32_cpu_csr_read(CSR_MIP) >> CSR_MIP_MEIP) & 1;
}

static void next_phase(void) {
    USB_DEVICE->control.f.enable_pu = 0;
    USB_DEVICE->control.f.enable_pu = 1;
}

int main(void) {
    while (USB_DEVICE->status.f.vbus_present == 0) { }

    usb_device__endpointx__control_t control = { .w = 0 };
    control.f.direction = 1; /* OUT, CONTROL, endpoint 0 */
    control.f.enable = 1;
    USB_DEVICE->endpoint[0].control.w = control.w;
    USB_DEVICE->endpoint[0].interrupt_enable.w = SETUP;
    USB_DEVICE->bus_event_enable.w = BUS_RESET;
    USB_DEVICE->control.f.enable_core = 1;
    expect(meip() == 0, 2);
    USB_DEVICE->control.f.enable_pu = 1;

    uint32_t i = 0;
    while ((USB_DEVICE->bus_event.w & BUS_RESET) == 0) {
        expect(++i < TIMEOUT, 10);
    }
    expect(USB_DEVICE->interrupt.w == USB_DEVICE__INTERRUPT__BUS_EVENT_bm, 11);
    expect(meip() == 0, 12);
    USB_DEVICE->bus_event.w = BUS_RESET;
    USB_DEVICE->interrupt_enable.w = USB_DEVICE__INTERRUPT_ENABLE__BUS_EVENT_ENABLE_bm;
    next_phase();

    for (i = 0; meip() == 0; ++i) {
        expect(i < TIMEOUT, 20);
    }
    expect(USB_DEVICE->interrupt.w == USB_DEVICE__INTERRUPT__BUS_EVENT_bm, 21);
    USB_DEVICE->bus_event.w = BUS_RESET;
    expect(USB_DEVICE->interrupt.w == 0, 22);
    expect(meip() == 0, 23);
    USB_DEVICE->interrupt_enable.w = USB_DEVICE__INTERRUPT_ENABLE__ENDPOINT_0_ENABLE_bm;
    next_phase();

    for (i = 0; meip() == 0; ++i) {
        expect(i < TIMEOUT, 30);
    }
    expect(USB_DEVICE->interrupt.w == USB_DEVICE__INTERRUPT__ENDPOINT_0_bm, 31);
    USB_DEVICE->endpoint[0].interrupt.w = SETUP;
    expect(USB_DEVICE->interrupt.w == 0, 32);
    expect(meip() == 0, 33);

    test_finish(1);
    return 0;
}
