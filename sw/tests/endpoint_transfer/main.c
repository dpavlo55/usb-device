#include <stdint.h>
#include "platform.h"
#include "../common/test_status.h"

/*
 * Paired with tb/cocotb/test_sw_endpoint_transfer.py. The pull-up is the
 * handshake: the host sends packets only after it rises.
 *   1. EP0 OUT armed, pull-up on. Host sends OUT_A (20 bytes), then OUT_B (5 bytes).
 *      OUT_A completes the transfer; OUT_B arrives unarmed and is an overrun.
 *   2. EP0 re-armed, pull-up toggled. Host sends a zero-length packet.
 */

#define BASE_ADDRESS 0x40u
#define OUT_A_LENGTH 20u
#define TIMEOUT 200000u

#define EP0 (&USB_DEVICE->endpoint[0])
#define INTERRUPT_ALL (USB_DEVICE__ENDPOINTX__INTERRUPT__SETUP_bm | \
                       USB_DEVICE__ENDPOINTX__INTERRUPT__CRC_ERROR_bm | \
                       USB_DEVICE__ENDPOINTX__INTERRUPT__TRANSFER_COMPLETE_bm | \
                       USB_DEVICE__ENDPOINTX__INTERRUPT__ZLP_bm | \
                       USB_DEVICE__ENDPOINTX__INTERRUPT__OVERRUN_bm)

static uint8_t out_a_byte(uint32_t i) {
    return (uint8_t)(7u * i + 3u);
}

static uint8_t ram_byte(uint32_t address) {
    const volatile uint32_t *const ram = (const volatile uint32_t *)(uintptr_t)USB_RAM_BASE;
    return (uint8_t)(ram[address / 4u] >> (8u * (address % 4u)));
}

static void expect(int condition, uint32_t status) {
    if (!condition) {
        test_finish(status);
    }
}

static uint32_t wait_interrupt(uint32_t mask, uint32_t status) {
    for (uint32_t i = 0; i < TIMEOUT; ++i) {
        const uint32_t pending = EP0->interrupt.w;
        if (pending & mask) {
            return pending;
        }
    }
    test_finish(status);
    return 0;
}

static void arm(void) {
    EP0->transfer.w = USB_DEVICE__ENDPOINTX__TRANSFER__ARM_bm;
    expect(EP0->status.f.armed == 1, 10);
}

static void check_out_a_in_ram(uint32_t status) {
    for (uint32_t i = 0; i < OUT_A_LENGTH; ++i) {
        expect(ram_byte(BASE_ADDRESS + i) == out_a_byte(i), status + i);
    }
}

int main(void) {
    while (USB_DEVICE->status.f.vbus_present == 0) { }

    usb_device__endpointx__control_t control = { .w = 0 };
    control.f.base_address = BASE_ADDRESS;
    control.f.size = 3; /* 64 bytes: buffers are 0x40 apart */
    control.f.direction = 1; /* OUT */
    control.f.enable = 1;
    EP0->control.w = control.w;
    EP0->interrupt_enable.w = INTERRUPT_ALL;
    USB_DEVICE->interrupt_enable.f.endpoint_0_enable = 1;
    USB_DEVICE->control.f.enable_core = 1;

    expect(EP0->interrupt.w == 0, 2);
    expect(EP0->status.f.armed == 0, 3);
    arm();
    USB_DEVICE->control.f.enable_pu = 1;

    /* OUT_A completes the armed transfer. */
    uint32_t pending = wait_interrupt(USB_DEVICE__ENDPOINTX__INTERRUPT__TRANSFER_COMPLETE_bm, 20);
    expect((pending & USB_DEVICE__ENDPOINTX__INTERRUPT__ZLP_bm) == 0, 21);
    expect(EP0->status.f.byte_count == OUT_A_LENGTH, 22);
    expect(EP0->status.f.armed == 0, 23);
    expect(USB_DEVICE->interrupt.f.endpoint_0 == 1, 24);
    check_out_a_in_ram(100);
    EP0->interrupt.w = USB_DEVICE__ENDPOINTX__INTERRUPT__TRANSFER_COMPLETE_bm;

    /* OUT_B finds the buffer unarmed: overrun, nothing written, status unchanged. */
    pending = wait_interrupt(USB_DEVICE__ENDPOINTX__INTERRUPT__OVERRUN_bm, 30);
    expect((pending & (USB_DEVICE__ENDPOINTX__INTERRUPT__TRANSFER_COMPLETE_bm |
                       USB_DEVICE__ENDPOINTX__INTERRUPT__ZLP_bm)) == 0, 31);
    expect(EP0->status.f.byte_count == OUT_A_LENGTH, 32);
    expect(EP0->status.f.armed == 0, 33);
    check_out_a_in_ram(200);
    EP0->interrupt.w = USB_DEVICE__ENDPOINTX__INTERRUPT__OVERRUN_bm;
    expect(EP0->interrupt.w == 0, 34);
    expect(USB_DEVICE->interrupt.f.endpoint_0 == 0, 35);

    /* Re-arm and signal the host with a pull-up toggle. */
    USB_DEVICE->control.f.enable_pu = 0;
    arm();
    USB_DEVICE->control.f.enable_pu = 1;

    /* A zero-length packet completes the transfer with zlp set. */
    pending = wait_interrupt(USB_DEVICE__ENDPOINTX__INTERRUPT__TRANSFER_COMPLETE_bm, 40);
    expect((pending & USB_DEVICE__ENDPOINTX__INTERRUPT__ZLP_bm) != 0, 41);
    expect((pending & USB_DEVICE__ENDPOINTX__INTERRUPT__OVERRUN_bm) == 0, 42);
    expect(EP0->status.f.byte_count == 0, 43);
    expect(EP0->status.f.armed == 0, 44);
    check_out_a_in_ram(300);

    test_finish(1);
    return 0;
}
