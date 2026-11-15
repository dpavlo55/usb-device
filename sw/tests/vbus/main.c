#include <stdint.h>
#include <neorv32.h>
#include "platform.h"
#include "usb_hal.h"
#include "../common/test_status.h"

/*
 * Paired with tb/cocotb/test_sw_vbus.py. The host plugs and unplugs VBUS; the
 * firmware reads the CPU's pending bit (mip.MEIP) without taking the trap.
 *   1. Boot without VBUS: vbus_present is 0. bus_event.vbus and the irq are
 *      enabled and enable_pu is set; the host checks that hardware keeps D+
 *      released (USB 2.0 section 7.1.5.1).
 *   2. VBUS appears: bus_event.vbus raises MEIP, vbus_present is 1; clearing the
 *      event drops MEIP. The host sees the pull-up rise without firmware action.
 *   3. VBUS goes away: bus_event.vbus again, vbus_present is 0; enable_pu still
 *      reads 1 while hardware releases D+.
 *   4. bus_event.vbus masked: VBUS appears, the event is set but MEIP stays clear.
 *   5. Through the HAL (initialized and connected): an unplug and a replug each give
 *      USB_HAL_EVENT_VBUS.
 */

#define TIMEOUT 2000000u
#define VBUS USB_DEVICE__BUS_EVENT__VBUS_bm

static void expect(int condition, uint32_t status) {
    if (!condition) {
        test_finish(status);
    }
}

static int meip(void) {
    return (neorv32_cpu_csr_read(CSR_MIP) >> CSR_MIP_MEIP) & 1;
}

static void wait_meip(uint32_t status) {
    for (uint32_t i = 0; meip() == 0; ++i) {
        expect(i < TIMEOUT, status);
    }
}

static uint32_t vbus_events;

static void on_event(usb_hal_t *hal, const usb_hal_event_t *event, void *ctx) {
    (void)hal;
    (void)ctx;
    if (event->type == USB_HAL_EVENT_VBUS) {
        ++vbus_events;
    }
}

static void wait_vbus_event(usb_hal_t *hal, uint32_t count, uint32_t status) {
    for (uint32_t i = 0; vbus_events < count; ++i) {
        expect(i < TIMEOUT, status);
        usb_hal_process(hal);
    }
}

int main(void) {
    expect(USB_DEVICE->status.f.vbus_present == 0, 2);
    USB_DEVICE->bus_event.w = VBUS;
    USB_DEVICE->bus_event_enable.w = USB_DEVICE__BUS_EVENT_ENABLE__VBUS_EN_bm;
    USB_DEVICE->interrupt_enable.w = USB_DEVICE__INTERRUPT_ENABLE__BUS_EVENT_ENABLE_bm;
    USB_DEVICE->control.f.enable_core = 1;
    USB_DEVICE->control.f.enable_pu = 1;
    expect(meip() == 0, 3);

    wait_meip(10);
    expect(USB_DEVICE->bus_event.w == VBUS, 11);
    expect(USB_DEVICE->status.f.vbus_present == 1, 12);
    expect(USB_DEVICE->interrupt.w == USB_DEVICE__INTERRUPT__BUS_EVENT_bm, 13);
    USB_DEVICE->bus_event.w = VBUS;
    expect(meip() == 0, 14);

    wait_meip(20);
    expect(USB_DEVICE->bus_event.w == VBUS, 21);
    expect(USB_DEVICE->status.f.vbus_present == 0, 22);
    expect(USB_DEVICE->control.f.enable_pu == 1, 23);
    USB_DEVICE->bus_event.w = VBUS;
    expect(meip() == 0, 24);

    USB_DEVICE->bus_event_enable.w = 0;
    for (uint32_t i = 0; USB_DEVICE->status.f.vbus_present == 0; ++i) {
        expect(i < TIMEOUT, 30);
    }
    expect(USB_DEVICE->bus_event.w == VBUS, 31);
    expect(meip() == 0, 32);

    usb_hal_t hal;
    expect(usb_hal_init(&hal, USB_DEVICE_BASE, on_event, NULL) == USB_OK, 40);
    usb_hal_connect(&hal, true);
    wait_vbus_event(&hal, 1, 41);
    expect(!usb_hal_vbus_present(&hal), 42);
    wait_vbus_event(&hal, 2, 43);
    expect(usb_hal_vbus_present(&hal), 44);

    test_finish(1);
    return 0;
}
