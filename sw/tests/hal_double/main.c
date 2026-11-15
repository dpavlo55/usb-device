#include <stdint.h>
#include "platform.h"
#include "usb_hal.h"
#include "../common/test_status.h"

/*
 * Double-buffered endpoints in the HAL, paired with tb/cocotb/test_sw_hal_double.py.
 * Bulk OUT 0x01 and bulk IN 0x81, 64 bytes, each on two entries. Each phase ends
 * with a pull-up toggle that lets the host start the next one; within a phase the
 * host expects every packet to be ACKed at once.
 *   0. Raw OUT: two packets armed, A and B arrive back to back. Both finish before
 *      usb_hal_process() runs; the events come in arrival order with A and B in
 *      their buffers.
 *   1. Raw OUT: one packet, C (entry 2). Then two armed, D lands in entry 3 and E in
 *      entry 2: the events still come in arrival order, D first.
 *   2. Raw IN: X and Y armed in the two buffers, sent back to back.
 *   3. Transfers: 150 bytes OUT and 150 bytes IN, one packet at a time.
 *   4. Bulk IN 0x82 opened with two 256-byte transfers (usb_hal_ep_open_ex): raw
 *      arms of 200 and 128 bytes go out as 64-byte packets, one event per arm.
 *   5. A 192-byte transfer with ZLP on 0x82: three full packets and a zero-length
 *      packet.
 */

#define EP_OUT 0x01u
#define EP_IN 0x81u
#define EP_SPLIT 0x82u
#define MAX_EVENTS 8u

#define ENTRY(k) (&USB_DEVICE->endpoint[(k)])

typedef struct {
    usb_hal_event_t event;
    uint8_t data[16];
} record_t;

static usb_hal_t hal;
static record_t records[MAX_EVENTS];
static uint32_t record_head;
static uint32_t record_tail;
static uint8_t rx[200] __attribute__((aligned(4)));
static uint8_t tx[200] __attribute__((aligned(4)));

static void expect(int condition, uint32_t status) {
    if (!condition) {
        test_finish(status);
    }
}

static uint8_t pattern(uint32_t n, uint32_t i) {
    return (uint8_t)((n << 4) + i);
}

/* OUT data is read from the finished buffer inside the callback, as a class does. */
static void on_event(usb_hal_t *h, const usb_hal_event_t *event, void *ctx) {
    (void)ctx;
    expect(record_head - record_tail < MAX_EVENTS, 2);
    record_t *r = &records[record_head++ % MAX_EVENTS];
    r->event = *event;
    if (event->type == USB_HAL_EVENT_XFER_DONE && event->ep_addr == EP_OUT) {
        const volatile uint32_t *buffer = usb_hal_ep_done_buffer(h, EP_OUT);
        for (uint32_t i = 0; i < sizeof r->data; ++i) {
            r->data[i] = (uint8_t)(buffer[i / 4u] >> (8u * (i % 4u)));
        }
    }
}

static record_t wait_done(uint8_t ep, uint32_t status) {
    for (uint32_t i = 0; record_head == record_tail; ++i) {
        expect(i < 2000000u, status);
        usb_hal_process(&hal);
    }
    const record_t r = records[record_tail++ % MAX_EVENTS];
    expect(r.event.type == USB_HAL_EVENT_XFER_DONE && r.event.ep_addr == ep, status + 1u);
    return r;
}

static void check_out(uint32_t n, uint32_t length, uint32_t status) {
    const record_t r = wait_done(EP_OUT, status);
    expect(r.event.length == length, status + 2u);
    for (uint32_t i = 0; i < length && i < sizeof r.data; ++i) {
        expect(r.data[i] == pattern(n, i), status + 3u);
    }
}

/* Hardware has finished both packets: neither entry of the endpoint is armed. */
static void wait_hardware(uint32_t k, uint32_t status) {
    for (uint32_t i = 0; ENTRY(k)->status.f.armed || ENTRY(k + 1u)->status.f.armed; ++i) {
        expect(i < 2000000u, status);
    }
}

static void fill_ep(uint8_t ep, uint32_t n, uint32_t length) {
    volatile uint32_t *buffer = usb_hal_ep_buffer(&hal, ep, NULL);
    for (uint32_t w = 0; w < (length + 3u) / 4u; ++w) {
        uint32_t word = 0;
        for (uint32_t b = 0; b < 4u; ++b) {
            word |= (uint32_t)pattern(n, 4u * w + b) << (8u * b);
        }
        buffer[w] = word;
    }
}

static void fill(uint32_t n, uint32_t length) {
    fill_ep(EP_IN, n, length);
}

static void next_phase(void) {
    usb_hal_connect(&hal, false);
    usb_hal_connect(&hal, true);
}

int main(void) {
    while (USB_DEVICE->status.f.vbus_present == 0) { }

    expect(usb_hal_init(&hal, USB_DEVICE_BASE, on_event, NULL) == USB_OK, 3);
    expect(usb_hal_ep_open_double(&hal, 0x00u, USB_EP_CONTROL, 64u) == USB_ERR_PARAM, 4);
    expect(usb_hal_ep_open_double(&hal, EP_OUT, USB_EP_BULK, 64u) == USB_OK, 5);
    expect(usb_hal_ep_open_double(&hal, EP_IN, USB_EP_BULK, 64u) == USB_OK, 6);
    expect(usb_hal_ep_open(&hal, EP_IN, USB_EP_BULK, 64u) == USB_ERR_PARAM, 7);
    expect(ENTRY(2)->control.f.double_buffer == 1 && ENTRY(3)->control.f.double_buffer == 0, 8);
    expect(ENTRY(4)->control.f.double_buffer == 1 && ENTRY(4)->control.f.direction == 0, 9);
    expect(ENTRY(3)->control.f.base_address == ENTRY(2)->control.f.base_address + 64u, 10);

    expect(usb_hal_ep_arm_raw(&hal, EP_OUT, 64u) == USB_OK, 11);
    expect(usb_hal_ep_arm_raw(&hal, EP_OUT, 64u) == USB_OK, 12);
    expect(usb_hal_ep_arm_raw(&hal, EP_OUT, 64u) == USB_ERR_BUSY, 13);
    usb_hal_connect(&hal, true);

    wait_hardware(2, 20);
    check_out(0xA, 10, 21);
    check_out(0xB, 11, 25);
    expect(usb_hal_ep_arm_raw(&hal, EP_OUT, 64u) == USB_OK, 29);
    next_phase();

    check_out(0xC, 12, 30);
    expect(usb_hal_ep_arm_raw(&hal, EP_OUT, 64u) == USB_OK, 34);
    expect(usb_hal_ep_arm_raw(&hal, EP_OUT, 64u) == USB_OK, 35);
    next_phase();
    wait_hardware(2, 36);
    check_out(0xD, 13, 40);
    check_out(0xE, 14, 44);

    fill(0x1, 5);
    expect(usb_hal_ep_arm_raw(&hal, EP_IN, 5u) == USB_OK, 50);
    fill(0x2, 7);
    expect(usb_hal_ep_arm_raw(&hal, EP_IN, 7u) == USB_OK, 51);
    expect(usb_hal_ep_arm_raw(&hal, EP_IN, 1u) == USB_ERR_BUSY, 52);
    next_phase();
    expect(wait_done(EP_IN, 53).event.length == 5u, 55);
    expect(wait_done(EP_IN, 56).event.length == 7u, 58);

    expect(usb_hal_ep_xfer(&hal, EP_OUT, rx, 150u, false) == USB_OK, 60);
    for (uint32_t i = 0; i < 150u; ++i) {
        tx[i] = pattern(0x4, i);
    }
    expect(usb_hal_ep_xfer(&hal, EP_IN, tx, 150u, false) == USB_OK, 61);
    next_phase();
    for (uint32_t done = 0; done < 2u; ++done) {
        const record_t r = wait_done(done == 0u ? EP_OUT : EP_IN, 62);
        expect(r.event.length == 150u, 64);
    }
    for (uint32_t i = 0; i < 150u; ++i) {
        expect(rx[i] == pattern(0x3, i), 65);
    }

    uint32_t size = 0u;
    expect(usb_hal_ep_open_ex(&hal, 0x02u, USB_EP_BULK, 64u, 256u, 1u) == USB_ERR_PARAM, 70);
    expect(usb_hal_ep_open_ex(&hal, EP_SPLIT, USB_EP_BULK, 64u, 256u, 2u) == USB_OK, 71);
    expect(usb_hal_ep_buffer(&hal, EP_SPLIT, &size) != NULL && size == 256u, 72);
    fill_ep(EP_SPLIT, 0x5, 200);
    expect(usb_hal_ep_arm_raw(&hal, EP_SPLIT, 200u) == USB_OK, 73);
    fill_ep(EP_SPLIT, 0x6, 128);
    expect(usb_hal_ep_arm_raw(&hal, EP_SPLIT, 128u) == USB_OK, 74);
    expect(usb_hal_ep_arm_raw(&hal, EP_SPLIT, 1u) == USB_ERR_BUSY, 75);
    next_phase();
    expect(wait_done(EP_SPLIT, 76).event.length == 200u, 78);
    expect(wait_done(EP_SPLIT, 79).event.length == 128u, 81);

    for (uint32_t i = 0; i < 192u; ++i) {
        tx[i] = pattern(0x7, i);
    }
    expect(usb_hal_ep_xfer(&hal, EP_SPLIT, tx, 192u, true) == USB_OK, 82);
    next_phase();
    expect(wait_done(EP_SPLIT, 83).event.length == 192u, 85);

    test_finish(1);
    return 0;
}
