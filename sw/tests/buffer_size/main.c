#include <stdint.h>
#include "platform.h"
#include "../common/test_status.h"

/*
 * Paired with tb/cocotb/test_sw_buffer_size.py. Every entry owns 2 ** (size + 3)
 * bytes; size 0 gives 8. Entry 2 is BULK OUT on endpoint 1 at 0x40, entry 3 is
 * BULK OUT on endpoint 2 directly after it at 0x48, entry 4 is BULK IN on endpoint 1
 * at 0x80. Each phase ends with a pull-up toggle that lets the host go on.
 *   1. OUT of 16 bytes to entry 2: no handshake, no event, entry 2 stays armed, and
 *      entry 3's buffer is untouched. Then 8 bytes complete as usual.
 *   2. Entry 4 is armed with transfer.length 20: the host receives 8 bytes.
 */

#define TIMEOUT 400000u
#define ENTRY_OUT 2u
#define ENTRY_NEIGHBOUR 3u
#define ENTRY_IN 4u
#define BASE_OUT 0x40u
#define BASE_NEIGHBOUR 0x48u
#define BASE_IN 0x80u
#define BUFFER_BYTES 8u
#define IN_LENGTH 20u

#define ENTRY(k) (&USB_DEVICE->endpoint[(k)])
#define INTERRUPT_ALL (USB_DEVICE__ENDPOINTX__INTERRUPT__SETUP_bm | \
                       USB_DEVICE__ENDPOINTX__INTERRUPT__CRC_ERROR_bm | \
                       USB_DEVICE__ENDPOINTX__INTERRUPT__TRANSFER_COMPLETE_bm | \
                       USB_DEVICE__ENDPOINTX__INTERRUPT__ZLP_bm | \
                       USB_DEVICE__ENDPOINTX__INTERRUPT__OVERRUN_bm)
#define COMPLETE USB_DEVICE__ENDPOINTX__INTERRUPT__TRANSFER_COMPLETE_bm

static volatile uint32_t *const ram = (volatile uint32_t *)(uintptr_t)USB_RAM_BASE;

static uint8_t ram_byte(uint32_t address) {
    return (uint8_t)(ram[address / 4u] >> (8u * (address % 4u)));
}

/* Fill [address, address + length) (word aligned) with (n << 4) + i. */
static void fill(uint32_t address, uint32_t length, uint32_t n) {
    for (uint32_t i = 0; i < length; i += 4u) {
        uint32_t word = 0;
        for (uint32_t b = 0; b < 4u; ++b) {
            word |= (uint32_t)(uint8_t)((n << 4) + i + b) << (8u * b);
        }
        ram[(address + i) / 4u] = word;
    }
}

static void expect(int condition, uint32_t status) {
    if (!condition) {
        test_finish(status);
    }
}

static void configure(uint32_t entry, uint32_t base, uint32_t number, uint32_t direction) {
    usb_device__endpointx__control_t control = { .w = 0 };
    control.f.base_address = base;
    control.f.size = 0; /* 8 bytes */
    control.f.endpoint_number = number;
    control.f.kind = 1; /* BULK */
    control.f.direction = direction;
    control.f.enable = 1;
    ENTRY(entry)->control.w = control.w;
    ENTRY(entry)->interrupt_enable.w = INTERRUPT_ALL;
}

static void wait_interrupt(uint32_t entry, uint32_t status) {
    for (uint32_t i = 0; i < TIMEOUT; ++i) {
        if (ENTRY(entry)->interrupt.w & COMPLETE) {
            return;
        }
    }
    test_finish(status);
}

static void next_phase(void) {
    USB_DEVICE->control.f.enable_pu = 0;
    USB_DEVICE->control.f.enable_pu = 1;
}

int main(void) {
    while (USB_DEVICE->status.f.vbus_present == 0) { }

    configure(ENTRY_OUT, BASE_OUT, 1, 1);
    configure(ENTRY_NEIGHBOUR, BASE_NEIGHBOUR, 2, 1);
    configure(ENTRY_IN, BASE_IN, 1, 0);
    fill(BASE_NEIGHBOUR, BUFFER_BYTES, 0xC);
    fill(BASE_IN, 32u, 0x5);
    USB_DEVICE->control.f.enable_core = 1;
    ENTRY(ENTRY_OUT)->transfer.w = USB_DEVICE__ENDPOINTX__TRANSFER__ARM_bm;
    USB_DEVICE->control.f.enable_pu = 1;

    /* The host sends 16 bytes (packet 0xA, no handshake), then 8 bytes (packet 0xB). */
    wait_interrupt(ENTRY_OUT, 10);
    expect(ENTRY(ENTRY_OUT)->interrupt.w == COMPLETE, 11);
    expect(ENTRY(ENTRY_OUT)->status.f.byte_count == BUFFER_BYTES, 12);
    for (uint32_t i = 0; i < BUFFER_BYTES; ++i) {
        expect(ram_byte(BASE_OUT + i) == (uint8_t)(0xB0u + i), 13);
        expect(ram_byte(BASE_NEIGHBOUR + i) == (uint8_t)(0xC0u + i), 14);
    }
    expect(ENTRY(ENTRY_NEIGHBOUR)->interrupt.w == 0, 15);
    ENTRY(ENTRY_OUT)->interrupt.w = COMPLETE;

    ENTRY(ENTRY_IN)->transfer.w = USB_DEVICE__ENDPOINTX__TRANSFER__ARM_bm |
                                  (IN_LENGTH << USB_DEVICE__ENDPOINTX__TRANSFER__LENGTH_bp);
    next_phase();
    wait_interrupt(ENTRY_IN, 20);
    expect(ENTRY(ENTRY_IN)->status.f.armed == 0, 21);

    test_finish(1);
    return 0;
}
