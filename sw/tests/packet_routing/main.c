#include <stdint.h>
#include "platform.h"
#include "../common/test_status.h"

/*
 * Paired with tb/cocotb/test_sw_packet_routing.py. The device has address 5 and
 * three entries: entry 2 is OUT on endpoint 1, entry 5 is OUT on endpoint 3 and
 * entry 7 is IN on endpoint 1. Entries 2 and 5 are armed, then the pull-up rises.
 * The host first sends DATA that is not for the device (after an OUT token to
 * address 6, and after an IN token to endpoint 1), then OUT DATA_3 to endpoint 3
 * and OUT DATA_1 to endpoint 1. Each must land in its entry's buffer only.
 */

#define DEVICE_ADDRESS 5u
#define TIMEOUT 400000u

#define ENTRY_EP1_OUT 2u
#define ENTRY_EP3_OUT 5u
#define ENTRY_EP1_IN 7u
#define BASE_EP1_OUT 0x40u
#define BASE_EP3_OUT 0x80u
#define BASE_EP1_IN 0xC0u
#define DATA_1_LENGTH 12u
#define DATA_3_LENGTH 7u

#define ENTRY(k) (&USB_DEVICE->endpoint[(k)])
#define INTERRUPT_ALL (USB_DEVICE__ENDPOINTX__INTERRUPT__SETUP_bm | \
                       USB_DEVICE__ENDPOINTX__INTERRUPT__CRC_ERROR_bm | \
                       USB_DEVICE__ENDPOINTX__INTERRUPT__TRANSFER_COMPLETE_bm | \
                       USB_DEVICE__ENDPOINTX__INTERRUPT__ZLP_bm | \
                       USB_DEVICE__ENDPOINTX__INTERRUPT__OVERRUN_bm)

static uint8_t data_1_byte(uint32_t i) { return (uint8_t)(0x10u + 3u * i); }
static uint8_t data_3_byte(uint32_t i) { return (uint8_t)(0xA0u ^ i); }

static uint8_t ram_byte(uint32_t address) {
    const volatile uint32_t *const ram = (const volatile uint32_t *)(uintptr_t)USB_RAM_BASE;
    return (uint8_t)(ram[address / 4u] >> (8u * (address % 4u)));
}

static void ram_write(uint32_t address, uint8_t value) {
    volatile uint8_t *const ram = (volatile uint8_t *)(uintptr_t)USB_RAM_BASE;
    ram[address] = value;
}

static void expect(int condition, uint32_t status) {
    if (!condition) {
        test_finish(status);
    }
}

static void configure(uint32_t entry, uint32_t base, uint32_t number, uint32_t direction) {
    usb_device__endpointx__control_t control = { .w = 0 };
    control.f.base_address = base;
    control.f.size = 3; /* 64 bytes: buffers are 0x40 apart */
    control.f.endpoint_number = number;
    control.f.kind = 1; /* BULK */
    control.f.direction = direction;
    control.f.enable = 1;
    ENTRY(entry)->control.w = control.w;
    ENTRY(entry)->interrupt_enable.w = INTERRUPT_ALL;
}

static void wait_complete(uint32_t entry, uint32_t status) {
    for (uint32_t i = 0; i < TIMEOUT; ++i) {
        if (ENTRY(entry)->interrupt.w & USB_DEVICE__ENDPOINTX__INTERRUPT__TRANSFER_COMPLETE_bm) {
            return;
        }
    }
    test_finish(status);
}

int main(void) {
    while (USB_DEVICE->status.f.vbus_present == 0) { }

    for (uint32_t i = 0; i < 64u; ++i) {
        ram_write(BASE_EP1_OUT + i, 0x55u);
        ram_write(BASE_EP3_OUT + i, 0x55u);
        ram_write(BASE_EP1_IN + i, 0x55u);
    }

    USB_DEVICE->device_address.w = DEVICE_ADDRESS;
    configure(ENTRY_EP1_OUT, BASE_EP1_OUT, 1, 1);
    configure(ENTRY_EP3_OUT, BASE_EP3_OUT, 3, 1);
    configure(ENTRY_EP1_IN, BASE_EP1_IN, 1, 0);
    USB_DEVICE->control.f.enable_core = 1;
    ENTRY(ENTRY_EP1_OUT)->transfer.w = USB_DEVICE__ENDPOINTX__TRANSFER__ARM_bm;
    ENTRY(ENTRY_EP3_OUT)->transfer.w = USB_DEVICE__ENDPOINTX__TRANSFER__ARM_bm;
    USB_DEVICE->control.f.enable_pu = 1;

    wait_complete(ENTRY_EP3_OUT, 10);
    wait_complete(ENTRY_EP1_OUT, 11);

    expect(ENTRY(ENTRY_EP3_OUT)->interrupt.w == USB_DEVICE__ENDPOINTX__INTERRUPT__TRANSFER_COMPLETE_bm, 20);
    expect(ENTRY(ENTRY_EP3_OUT)->status.f.byte_count == DATA_3_LENGTH, 21);
    expect(ENTRY(ENTRY_EP1_OUT)->interrupt.w == USB_DEVICE__ENDPOINTX__INTERRUPT__TRANSFER_COMPLETE_bm, 22);
    expect(ENTRY(ENTRY_EP1_OUT)->status.f.byte_count == DATA_1_LENGTH, 23);
    expect(ENTRY(ENTRY_EP1_IN)->interrupt.w == 0, 24);
    expect(ENTRY(ENTRY_EP1_IN)->status.f.armed == 0, 25);

    for (uint32_t i = 0; i < DATA_3_LENGTH; ++i) {
        expect(ram_byte(BASE_EP3_OUT + i) == data_3_byte(i), 100 + i);
    }
    for (uint32_t i = 0; i < DATA_1_LENGTH; ++i) {
        expect(ram_byte(BASE_EP1_OUT + i) == data_1_byte(i), 200 + i);
    }
    for (uint32_t i = 0; i < 64u; ++i) {
        expect(ram_byte(BASE_EP1_IN + i) == 0x55u, 300 + i);
    }

    test_finish(1);
    return 0;
}
