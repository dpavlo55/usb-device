#include <stdint.h>
#include "platform.h"
#include "../common/test_status.h"

/*
 * Paired with tb/cocotb/test_sw_ram_size.py. The RAM size comes from the generated
 * header, so the test follows RAM_ADDR_WIDTH in rdl/usb_device.rdl:
 *   1. status.ram_words reads the size in the header, and every word
 *      holds its own value (no address bit is lost, so nothing aliases).
 *   2. An OUT packet lands in the last 64 bytes of the RAM and an IN packet is sent
 *      from the 64 bytes below them.
 */

#define RAM_BYTES ((uint32_t)sizeof(usb_device__usb_ram_t))
#define BASE_OUT (RAM_BYTES - 0x40u)
#define BASE_IN (RAM_BYTES - 0x80u)
#define TIMEOUT 400000u
#define COMPLETE USB_DEVICE__ENDPOINTX__INTERRUPT__TRANSFER_COMPLETE_bm

#define RAM ((volatile uint32_t *)(uintptr_t)USB_RAM_BASE)
#define ENTRY(k) (&USB_DEVICE->endpoint[(k)])

static void expect(int condition, uint32_t status) {
    if (!condition) {
        test_finish(status);
    }
}

static void configure(uint32_t entry, uint32_t base, uint32_t direction) {
    usb_device__endpointx__control_t control = { .w = 0 };
    control.f.base_address = base;
    control.f.endpoint_number = 1;
    control.f.kind = 1; /* BULK */
    control.f.size = 3; /* 64 bytes */
    control.f.direction = direction;
    control.f.enable = 1;
    ENTRY(entry)->control.w = control.w;
}

static void wait_complete(uint32_t entry, uint32_t status) {
    for (uint32_t i = 0; i < TIMEOUT; ++i) {
        if (ENTRY(entry)->interrupt.w & COMPLETE) {
            return;
        }
    }
    test_finish(status);
}

int main(void) {
    while (USB_DEVICE->status.f.vbus_present == 0) { }

    expect(USB_DEVICE->status.f.ram_words * 4u == RAM_BYTES, 2);
    for (uint32_t i = 0; i < RAM_BYTES / 4u; ++i) {
        RAM[i] = 0xA5000000u ^ (i * 0x00010001u);
    }
    for (uint32_t i = 0; i < RAM_BYTES / 4u; ++i) {
        expect(RAM[i] == (0xA5000000u ^ (i * 0x00010001u)), 3);
    }

    for (uint32_t i = 0; i < 16u; ++i) {
        RAM[BASE_IN / 4u + i] = 0x03020100u + i * 0x04040404u;
    }
    configure(2, BASE_OUT, 1);
    configure(3, BASE_IN, 0);
    USB_DEVICE->control.f.enable_core = 1;
    ENTRY(2)->transfer.w = USB_DEVICE__ENDPOINTX__TRANSFER__ARM_bm;
    ENTRY(3)->transfer.w = USB_DEVICE__ENDPOINTX__TRANSFER__ARM_bm | (64u << USB_DEVICE__ENDPOINTX__TRANSFER__LENGTH_bp);
    USB_DEVICE->control.f.enable_pu = 1;

    wait_complete(2, 10);
    expect(ENTRY(2)->status.f.byte_count == 64u, 11);
    for (uint32_t i = 0; i < 16u; ++i) {
        expect(RAM[BASE_OUT / 4u + i] == ~(0x03020100u + i * 0x04040404u), 12);
    }
    wait_complete(3, 20);

    test_finish(1);
    return 0;
}
