#include <stdint.h>
#include "platform.h"
#include "../common/test_status.h"

/*
 * Paired with tb/cocotb/test_sw_in_split.py. IN transfers split into packets
 * (control.split). Entry 2 is BULK IN endpoint 1 with a 128-byte buffer sent as
 * 64-byte packets; entries 4 (double_buffer) and 5 are BULK IN endpoint 2, the same
 * on two buffers. Each phase ends with a pull-up toggle that lets the host start the
 * next one.
 *   0. Endpoint 1, 100 bytes: 64 + 36, one completion after the short packet.
 *   1. Endpoint 1, 128 bytes: 64 + 64, completes on the last byte; no zero-length
 *      packet follows (the host's next IN gets NAK).
 *   2. Endpoint 2, both buffers with 128 bytes: four packets in a row, entry 4's two
 *      first, a completion for each buffer and next_buffer back at 0.
 */

#define TIMEOUT 400000u
#define ENTRY(k) (&USB_DEVICE->endpoint[(k)])
#define BASE(k) (0x80u * (k))
#define COMPLETE USB_DEVICE__ENDPOINTX__INTERRUPT__TRANSFER_COMPLETE_bm

static volatile uint32_t *const ram = (volatile uint32_t *)(uintptr_t)USB_RAM_BASE;

static void expect(int condition, uint32_t status) {
    if (!condition) {
        test_finish(status);
    }
}

static void configure(uint32_t entry, uint32_t number, uint32_t double_buffer) {
    usb_device__endpointx__control_t control = { .w = 0 };
    control.f.base_address = BASE(entry);
    control.f.split = 1; /* 128-byte buffer, 64-byte packets */
    control.f.double_buffer = double_buffer;
    control.f.size = 4;
    control.f.endpoint_number = number;
    control.f.kind = 1; /* BULK */
    control.f.direction = 0; /* IN */
    control.f.enable = 1;
    ENTRY(entry)->control.w = control.w;
    ENTRY(entry)->interrupt_enable.w = COMPLETE;
}

/* Byte i of the buffer of entry k is (k << 4) + i. */
static void fill(uint32_t entry) {
    for (uint32_t w = 0; w < 32u; ++w) {
        uint32_t word = 0;
        for (uint32_t b = 0; b < 4u; ++b) {
            word |= (uint32_t)(uint8_t)((entry << 4) + 4u * w + b) << (8u * b);
        }
        ram[BASE(entry) / 4u + w] = word;
    }
}

static void arm(uint32_t entry, uint32_t length) {
    ENTRY(entry)->transfer.w = USB_DEVICE__ENDPOINTX__TRANSFER__ARM_bm | length;
}

static void take(uint32_t entry, uint32_t status) {
    for (uint32_t i = 0; (ENTRY(entry)->interrupt.w & COMPLETE) == 0u; ++i) {
        expect(i < TIMEOUT, status);
    }
    expect(ENTRY(entry)->status.f.armed == 0, status + 1u);
    ENTRY(entry)->interrupt.w = COMPLETE;
}

static void next_phase(void) {
    USB_DEVICE->control.f.enable_pu = 0;
    USB_DEVICE->control.f.enable_pu = 1;
}

int main(void) {
    while (USB_DEVICE->status.f.vbus_present == 0) { }

    configure(2, 1, 0);
    configure(4, 2, 1);
    configure(5, 2, 0);
    fill(2);
    fill(4);
    fill(5);
    USB_DEVICE->control.f.enable_core = 1;
    arm(2, 100);
    USB_DEVICE->control.f.enable_pu = 1;

    take(2, 10);
    arm(2, 128);
    next_phase();

    take(2, 20);
    arm(4, 128);
    arm(5, 128);
    next_phase();

    take(4, 30);
    take(5, 40);
    expect(ENTRY(4)->status.f.next_buffer == 0, 45);

    test_finish(1);
    return 0;
}
