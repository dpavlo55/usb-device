#include <stdint.h>
#include "platform.h"
#include "../common/test_status.h"

/*
 * Paired with tb/cocotb/test_sw_setup.py. EP0 is entry 0 (OUT) and entry 1 (IN);
 * entry 2 is OUT on endpoint 1. Entries 1 and 2 are armed, entries 0 and 1 are
 * stalled, then the pull-up rises and the host sends SETUP_1.
 *   1. SETUP_1 is ACKed although EP0 is stalled: interrupt.setup, its 8 bytes at
 *      the start of entry 0's buffer, byte_count 8, stall cleared on entries 0 and
 *      1, entry 1 disarmed, entry 2 still armed.
 *   2. Buffer bytes 8 to 15 get a pattern, pull-up toggled. The host sends SETUPs
 *      that must not be answered (bad CRC, DATA1, 7 bytes, 12 bytes), all with other
 *      bytes than SETUP_1, then 1 byte OUT to endpoint 1 as a doorbell. None of the
 *      rejected SETUPs may have written the buffer: it still holds SETUP_1 and the
 *      pattern, and no SETUP event was raised.
 *   3. Pull-up toggled. SETUP_2: its 8 bytes are in the buffer, the pattern after
 *      them is untouched.
 */

#define TIMEOUT 400000u
#define BASE_EP0_OUT 0x00u
#define BASE_EP0_IN 0x40u
#define BASE_EP1_OUT 0x80u

#define ENTRY(k) (&USB_DEVICE->endpoint[(k)])
#define INTERRUPT_ALL (USB_DEVICE__ENDPOINTX__INTERRUPT__SETUP_bm | \
                       USB_DEVICE__ENDPOINTX__INTERRUPT__CRC_ERROR_bm | \
                       USB_DEVICE__ENDPOINTX__INTERRUPT__TRANSFER_COMPLETE_bm | \
                       USB_DEVICE__ENDPOINTX__INTERRUPT__ZLP_bm | \
                       USB_DEVICE__ENDPOINTX__INTERRUPT__OVERRUN_bm)

static const uint8_t SETUP_1[8] = { 0x80, 0x06, 0x00, 0x01, 0x00, 0x00, 0x40, 0x00 };
static const uint8_t SETUP_2[8] = { 0x00, 0x05, 0x07, 0x00, 0x00, 0x00, 0x00, 0x00 };

static uint8_t ram_byte(uint32_t address) {
    const volatile uint32_t *const ram = (const volatile uint32_t *)(uintptr_t)USB_RAM_BASE;
    return (uint8_t)(ram[address / 4u] >> (8u * (address % 4u)));
}

static void expect(int condition, uint32_t status) {
    if (!condition) {
        test_finish(status);
    }
}

static void configure(uint32_t entry, uint32_t base, uint32_t number, uint32_t kind, uint32_t direction) {
    usb_device__endpointx__control_t control = { .w = 0 };
    control.f.base_address = base;
    control.f.size = 3; /* 64 bytes: buffers are 0x40 apart */
    control.f.endpoint_number = number;
    control.f.kind = kind;
    control.f.direction = direction;
    control.f.enable = 1;
    ENTRY(entry)->control.w = control.w;
    ENTRY(entry)->interrupt_enable.w = INTERRUPT_ALL;
}

static void wait_setup(uint32_t status) {
    for (uint32_t i = 0; i < TIMEOUT; ++i) {
        if (ENTRY(0)->interrupt.w & USB_DEVICE__ENDPOINTX__INTERRUPT__SETUP_bm) {
            return;
        }
    }
    test_finish(status);
}

static void fill_pattern(void) {
    volatile uint32_t *const ram = (volatile uint32_t *)(uintptr_t)USB_RAM_BASE;
    ram[(BASE_EP0_OUT + 8u) / 4u] = 0xE3E2E1E0u;
    ram[(BASE_EP0_OUT + 12u) / 4u] = 0xE7E6E5E4u;
}

static void check_pattern(uint32_t status) {
    for (uint32_t i = 8u; i < 16u; ++i) {
        expect(ram_byte(BASE_EP0_OUT + i) == (uint8_t)(0xD8u + i), status);
    }
}

static void check_setup_bytes(const uint8_t *expected, uint32_t status) {
    expect(ENTRY(0)->status.f.byte_count == 8u, status);
    for (uint32_t i = 0; i < 8u; ++i) {
        expect(ram_byte(BASE_EP0_OUT + i) == expected[i], status + 1u + i);
    }
}

int main(void) {
    while (USB_DEVICE->status.f.vbus_present == 0) { }

    configure(0, BASE_EP0_OUT, 0, 0, 1);
    configure(1, BASE_EP0_IN, 0, 0, 0);
    configure(2, BASE_EP1_OUT, 1, 1, 1);
    USB_DEVICE->control.f.enable_core = 1;
    ENTRY(1)->transfer.w = USB_DEVICE__ENDPOINTX__TRANSFER__ARM_bm | (4u << USB_DEVICE__ENDPOINTX__TRANSFER__LENGTH_bp);
    ENTRY(2)->transfer.w = USB_DEVICE__ENDPOINTX__TRANSFER__ARM_bm;
    ENTRY(0)->control.f.stall = 1;
    ENTRY(1)->control.f.stall = 1;
    expect(ENTRY(1)->status.f.armed == 1, 2);
    expect(ENTRY(2)->status.f.armed == 1, 3);
    USB_DEVICE->control.f.enable_pu = 1;

    wait_setup(10);
    expect(ENTRY(0)->interrupt.w == USB_DEVICE__ENDPOINTX__INTERRUPT__SETUP_bm, 11);
    expect(ENTRY(1)->interrupt.w == 0, 12);
    expect(ENTRY(2)->interrupt.w == 0, 13);
    expect(ENTRY(0)->control.f.stall == 0, 14);
    expect(ENTRY(1)->control.f.stall == 0, 15);
    expect(ENTRY(1)->status.f.armed == 0, 16);
    expect(ENTRY(2)->status.f.armed == 1, 17);
    check_setup_bytes(SETUP_1, 20);
    ENTRY(0)->interrupt.w = USB_DEVICE__ENDPOINTX__INTERRUPT__SETUP_bm;
    expect(ENTRY(0)->interrupt.w == 0, 30);

    fill_pattern();
    USB_DEVICE->control.f.enable_pu = 0;
    USB_DEVICE->control.f.enable_pu = 1;

    for (uint32_t i = 0; (ENTRY(2)->interrupt.w & USB_DEVICE__ENDPOINTX__INTERRUPT__TRANSFER_COMPLETE_bm) == 0; ++i) {
        expect(i < TIMEOUT, 31);
    }
    expect(ENTRY(0)->interrupt.w == 0, 32);
    for (uint32_t i = 0; i < 8u; ++i) {
        expect(ram_byte(BASE_EP0_OUT + i) == SETUP_1[i], 33);
    }
    check_pattern(34);
    USB_DEVICE->control.f.enable_pu = 0;
    USB_DEVICE->control.f.enable_pu = 1;

    wait_setup(40);
    expect(ENTRY(0)->interrupt.w == USB_DEVICE__ENDPOINTX__INTERRUPT__SETUP_bm, 41);
    check_setup_bytes(SETUP_2, 50);
    check_pattern(60);

    test_finish(1);
    return 0;
}
