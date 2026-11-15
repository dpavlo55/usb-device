#include <stdint.h>
#include "platform.h"
#include "usb_hal.h"
#include "../common/test_status.h"

/*
 * Layer 2 of the USB device library, paired with tb/cocotb/test_sw_usb_hal.py. The
 * firmware polls usb_hal_process(); the host retries on NAK, so each step simply
 * waits for the event it expects. Bulk OUT 0x01 and bulk IN 0x81, 64 bytes.
 */

#define EP_OUT 0x01u
#define EP_IN 0x81u
#define MAX_EVENTS 8u

static usb_hal_t hal;
static usb_hal_event_t events[MAX_EVENTS];
static uint32_t event_head;
static uint32_t event_tail;
static uint8_t rx[200] __attribute__((aligned(4)));
static uint8_t tx[200] __attribute__((aligned(4)));

static void expect(int condition, uint32_t status) {
    if (!condition) {
        test_finish(status);
    }
}

static void on_event(usb_hal_t *h, const usb_hal_event_t *event, void *ctx) {
    (void)h;
    (void)ctx;
    expect(event_head - event_tail < MAX_EVENTS, 2);
    events[event_head++ % MAX_EVENTS] = *event;
}

/* Process until the next event; it must be of @p type (and @p ep for XFER_DONE). */
static usb_hal_event_t wait_event(usb_hal_event_type_t type, uint8_t ep, uint32_t status) {
    for (uint32_t i = 0; event_head == event_tail; ++i) {
        expect(i < 2000000u, status);
        usb_hal_process(&hal);
    }
    const usb_hal_event_t event = events[event_tail++ % MAX_EVENTS];
    expect(event.type == type, status + 1u);
    expect(type != USB_HAL_EVENT_XFER_DONE || event.ep_addr == ep, status + 2u);
    return event;
}

static uint8_t pattern(uint32_t n, uint32_t i) {
    return (uint8_t)((n << 4) + i);
}

static void check_rx(uint32_t n, uint32_t length, uint32_t status) {
    for (uint32_t i = 0; i < length; ++i) {
        expect(rx[i] == pattern(n, i), status);
    }
}

static void send(uint32_t n, uint32_t length, bool zlp, uint32_t status) {
    for (uint32_t i = 0; i < length; ++i) {
        tx[i] = pattern(n, i);
    }
    expect(usb_hal_ep_xfer(&hal, EP_IN, tx, length, zlp) == USB_OK, status);
    expect(wait_event(USB_HAL_EVENT_XFER_DONE, EP_IN, status + 1u).length == length, status + 4u);
}

static void receive(uint32_t n, uint32_t capacity, uint32_t expected, uint32_t status) {
    expect(usb_hal_ep_xfer(&hal, EP_OUT, rx, capacity, false) == USB_OK, status);
    expect(wait_event(USB_HAL_EVENT_XFER_DONE, EP_OUT, status + 1u).length == expected, status + 4u);
    check_rx(n, expected, status + 5u);
}

int main(void) {
    while (USB_DEVICE->status.f.vbus_present == 0) { }

    expect(usb_hal_init(&hal, USB_DEVICE_BASE, on_event, NULL) == USB_OK, 3);
    expect(usb_hal_ep_open(&hal, EP_OUT, USB_EP_BULK, 64u) == USB_OK, 4);
    expect(usb_hal_ep_open(&hal, EP_IN, USB_EP_BULK, 64u) == USB_OK, 5);
    expect(usb_hal_ep_xfer(&hal, 0x02u, rx, 1u, false) == USB_ERR_STATE, 6);
    usb_hal_connect(&hal, true);

    receive(0x1, 150u, 150u, 10);
    send(0x2, 150u, false, 20);
    send(0x3, 128u, true, 30);
    receive(0x4, 64u, 64u, 40);
    receive(0x5, 100u, 10u, 50);

    expect(usb_hal_ep_stall(&hal, EP_IN, true) == USB_OK, 60);
    expect(usb_hal_ep_stalled(&hal, EP_IN), 61);
    receive(0x6, 8u, 1u, 62);
    expect(usb_hal_ep_stall(&hal, EP_IN, false) == USB_OK, 68);
    send(0x7, 4u, false, 70);

    usb_hal_event_t setup = wait_event(USB_HAL_EVENT_SETUP, 0u, 80);
    expect(setup.setup[0] == 0x80u && setup.setup[1] == 0x06u && setup.setup[6] == 0x12u, 83);
    usb_hal_ep0_stall(&hal);
    setup = wait_event(USB_HAL_EVENT_SETUP, 0u, 84);
    expect(setup.setup[0] == 0x80u && setup.setup[1] == 0x00u && setup.setup[6] == 0x02u, 87);
    static uint8_t status_bytes[2] = { 0x01u, 0x00u };
    expect(usb_hal_ep_xfer(&hal, 0x00u, NULL, 0u, false) == USB_OK, 88);
    expect(usb_hal_ep_xfer(&hal, 0x80u, status_bytes, 2u, false) == USB_OK, 89);
    expect(wait_event(USB_HAL_EVENT_XFER_DONE, 0x80u, 90).length == 2u, 93);
    expect(wait_event(USB_HAL_EVENT_XFER_DONE, 0x00u, 94).length == 0u, 97);

    uint32_t size = 0u;
    volatile uint32_t *buffer = usb_hal_ep_buffer(&hal, EP_IN, &size);
    expect(buffer != NULL && size == 64u, 100);
    buffer[0] = 0x03020100u;
    buffer[1] = 0x07060504u;
    expect(usb_hal_ep_arm_raw(&hal, EP_IN, 8u) == USB_OK, 101);
    expect(wait_event(USB_HAL_EVENT_XFER_DONE, EP_IN, 102).length == 8u, 105);

    wait_event(USB_HAL_EVENT_BUS_RESET, 0u, 110);
    expect(usb_hal_ep_xfer(&hal, EP_IN, tx, 1u, false) == USB_ERR_STATE, 113);
    expect(usb_hal_ep_open(&hal, EP_IN, USB_EP_BULK, 64u) == USB_OK, 114);

    test_finish(1);
    return 0;
}
