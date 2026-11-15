#include <stdint.h>
#include "platform.h"
#include "../common/test_status.h"

/*
 * Paired with tb/cocotb/test_sw_double_buffer.py. Entries 2 (double_buffer) and 3
 * are the two buffers of BULK OUT endpoint 1, entries 4 (double_buffer) and 5 the
 * two buffers of BULK IN endpoint 1. Each phase ends with a pull-up toggle that
 * lets the host start the next one; the host checks the handshakes, the firmware
 * checks the events, the buffers and status.next_buffer.
 *   0. Both armed: A goes to entry 2, B to entry 3, both ACKed back to back; C
 *      finds entry 2 unarmed: NAK, overrun on entry 2.
 *   1. Only entry 3 armed: C is still entry 2's turn: NAK, overrun on entry 2.
 *   2. Both armed: C to entry 2, D to entry 3.
 *   3. Only entry 2 armed: E to entry 2 (next_buffer 1), F gets NAK, overrun on 3.
 *   4. Entry 3 armed: F to entry 3 (next_buffer 0).
 *   5. IN: X from entry 4 (DATA0), Y from entry 5 (DATA1), then NAK.
 */

#define TIMEOUT 400000u
#define ENTRY_OUT 2u
#define ENTRY_IN 4u

#define ENTRY(k) (&USB_DEVICE->endpoint[(k)])
#define BASE(k) (0x40u * (k))
#define INTERRUPT_ALL (USB_DEVICE__ENDPOINTX__INTERRUPT__SETUP_bm | \
                       USB_DEVICE__ENDPOINTX__INTERRUPT__CRC_ERROR_bm | \
                       USB_DEVICE__ENDPOINTX__INTERRUPT__TRANSFER_COMPLETE_bm | \
                       USB_DEVICE__ENDPOINTX__INTERRUPT__ZLP_bm | \
                       USB_DEVICE__ENDPOINTX__INTERRUPT__OVERRUN_bm)
#define COMPLETE USB_DEVICE__ENDPOINTX__INTERRUPT__TRANSFER_COMPLETE_bm
#define OVERRUN USB_DEVICE__ENDPOINTX__INTERRUPT__OVERRUN_bm

static volatile uint32_t *const ram = (volatile uint32_t *)(uintptr_t)USB_RAM_BASE;

static uint8_t ram_byte(uint32_t address) {
    return (uint8_t)(ram[address / 4u] >> (8u * (address % 4u)));
}

static void expect(int condition, uint32_t status) {
    if (!condition) {
        test_finish(status);
    }
}

static void configure(uint32_t entry, uint32_t direction, uint32_t double_buffer) {
    usb_device__endpointx__control_t control = { .w = 0 };
    control.f.base_address = BASE(entry);
    control.f.double_buffer = double_buffer;
    control.f.size = 3; /* 64 bytes */
    control.f.endpoint_number = 1;
    control.f.kind = 1; /* BULK */
    control.f.direction = direction;
    control.f.enable = 1;
    ENTRY(entry)->control.w = control.w;
    ENTRY(entry)->interrupt_enable.w = INTERRUPT_ALL;
}

static void arm(uint32_t entry, uint32_t length) {
    ENTRY(entry)->transfer.w = USB_DEVICE__ENDPOINTX__TRANSFER__ARM_bm | length;
}

static uint32_t next_buffer(uint32_t entry) {
    return ENTRY(entry)->status.f.next_buffer;
}

static void wait_interrupt(uint32_t entry, uint32_t mask, uint32_t status) {
    for (uint32_t i = 0; i < TIMEOUT; ++i) {
        if (ENTRY(entry)->interrupt.w & mask) {
            return;
        }
    }
    test_finish(status);
}

static void take(uint32_t entry, uint32_t mask, uint32_t status) {
    wait_interrupt(entry, mask, status);
    expect(ENTRY(entry)->interrupt.w == mask, status + 1u);
    ENTRY(entry)->interrupt.w = mask;
}

/* Packet n of the host carries bytes (n << 4) + i. */
static void check_packet(uint32_t entry, uint32_t n, uint32_t length, uint32_t status) {
    take(entry, COMPLETE, status);
    expect(ENTRY(entry)->status.f.byte_count == length, status + 2u);
    expect(ENTRY(entry)->status.f.armed == 0, status + 3u);
    for (uint32_t i = 0; i < length; ++i) {
        expect(ram_byte(BASE(entry) + i) == (uint8_t)((n << 4) + i), status + 4u);
    }
}

static void fill(uint32_t entry, uint32_t n, uint32_t length) {
    for (uint32_t w = 0; w < (length + 3u) / 4u; ++w) {
        uint32_t word = 0;
        for (uint32_t b = 0; b < 4u; ++b) {
            word |= (uint32_t)(uint8_t)((n << 4) + 4u * w + b) << (8u * b);
        }
        ram[BASE(entry) / 4u + w] = word;
    }
}

static void next_phase(void) {
    USB_DEVICE->control.f.enable_pu = 0;
    USB_DEVICE->control.f.enable_pu = 1;
}

int main(void) {
    while (USB_DEVICE->status.f.vbus_present == 0) { }

    configure(ENTRY_OUT, 1, 1);
    configure(ENTRY_OUT + 1u, 1, 0);
    configure(ENTRY_IN, 0, 1);
    configure(ENTRY_IN + 1u, 0, 0);
    USB_DEVICE->control.f.enable_core = 1;
    expect(next_buffer(ENTRY_OUT) == 0, 2);
    arm(ENTRY_OUT, 0);
    arm(ENTRY_OUT + 1u, 0);
    USB_DEVICE->control.f.enable_pu = 1;

    check_packet(ENTRY_OUT, 0xA, 10, 10);
    check_packet(ENTRY_OUT + 1u, 0xB, 11, 20);
    take(ENTRY_OUT, OVERRUN, 30);
    expect(next_buffer(ENTRY_OUT) == 0, 33);
    expect(ENTRY(ENTRY_OUT + 1u)->interrupt.w == 0, 34);
    arm(ENTRY_OUT + 1u, 0);
    next_phase();

    take(ENTRY_OUT, OVERRUN, 40);
    expect(ENTRY(ENTRY_OUT + 1u)->status.f.armed == 1, 43);
    expect(ENTRY(ENTRY_OUT + 1u)->interrupt.w == 0, 44);
    arm(ENTRY_OUT, 0);
    next_phase();

    check_packet(ENTRY_OUT, 0xC, 12, 50);
    check_packet(ENTRY_OUT + 1u, 0xD, 13, 60);
    expect(next_buffer(ENTRY_OUT) == 0, 65);
    arm(ENTRY_OUT, 0);
    next_phase();

    check_packet(ENTRY_OUT, 0xE, 14, 70);
    expect(next_buffer(ENTRY_OUT) == 1, 75);
    take(ENTRY_OUT + 1u, OVERRUN, 80);
    expect(next_buffer(ENTRY_OUT) == 1, 83);
    arm(ENTRY_OUT + 1u, 0);
    next_phase();

    check_packet(ENTRY_OUT + 1u, 0xF, 15, 90);
    expect(next_buffer(ENTRY_OUT) == 0, 95);

    fill(ENTRY_IN, 0x1, 5);
    fill(ENTRY_IN + 1u, 0x2, 7);
    arm(ENTRY_IN, 5);
    arm(ENTRY_IN + 1u, 7);
    next_phase();

    take(ENTRY_IN, COMPLETE, 100);
    take(ENTRY_IN + 1u, COMPLETE, 110);
    expect(next_buffer(ENTRY_IN) == 0, 115);

    test_finish(1);
    return 0;
}
