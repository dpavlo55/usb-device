#include <stdint.h>
#include "platform.h"
#include "../common/test_status.h"

/*
 * Paired with tb/cocotb/test_sw_out_handshake.py. Entry 2 is BULK OUT on
 * endpoint 1, entry 3 is BULK OUT on endpoint 2 (a doorbell). Each phase ends
 * with a pull-up toggle that lets the host start the next one; the host checks
 * the handshakes, the firmware checks the registers and the buffer.
 *   1. Armed: A (DATA0) completes. B (DATA0) finds the entry unarmed: NAK, overrun.
 *   2. Re-armed: C (DATA0, the old toggle) is ACKed and dropped; D (DATA1) completes.
 *   3. Re-armed and stalled: STALL, no answer to a bad CRC, then the doorbell; no
 *      event on entry 2, which stays armed.
 *   4. Stall cleared: G (DATA0) completes.
 *   5. Re-armed after reset_toggle: H (DATA0) completes.
 */

#define TIMEOUT 400000u
#define ENTRY_OUT 2u
#define ENTRY_DOORBELL 3u
#define BASE_OUT 0x40u
#define BASE_DOORBELL 0x80u

#define ENTRY(k) (&USB_DEVICE->endpoint[(k)])
#define INTERRUPT_ALL (USB_DEVICE__ENDPOINTX__INTERRUPT__SETUP_bm | \
                       USB_DEVICE__ENDPOINTX__INTERRUPT__CRC_ERROR_bm | \
                       USB_DEVICE__ENDPOINTX__INTERRUPT__TRANSFER_COMPLETE_bm | \
                       USB_DEVICE__ENDPOINTX__INTERRUPT__ZLP_bm | \
                       USB_DEVICE__ENDPOINTX__INTERRUPT__OVERRUN_bm)
#define COMPLETE USB_DEVICE__ENDPOINTX__INTERRUPT__TRANSFER_COMPLETE_bm
#define OVERRUN USB_DEVICE__ENDPOINTX__INTERRUPT__OVERRUN_bm

static uint8_t ram_byte(uint32_t address) {
    const volatile uint32_t *const ram = (const volatile uint32_t *)(uintptr_t)USB_RAM_BASE;
    return (uint8_t)(ram[address / 4u] >> (8u * (address % 4u)));
}

static void expect(int condition, uint32_t status) {
    if (!condition) {
        test_finish(status);
    }
}

static void configure(uint32_t entry, uint32_t base, uint32_t number) {
    usb_device__endpointx__control_t control = { .w = 0 };
    control.f.base_address = base;
    control.f.size = 3; /* 64 bytes: buffers are 0x40 apart */
    control.f.endpoint_number = number;
    control.f.kind = 1; /* BULK */
    control.f.direction = 1; /* OUT */
    control.f.enable = 1;
    ENTRY(entry)->control.w = control.w;
    ENTRY(entry)->interrupt_enable.w = INTERRUPT_ALL;
}

static void arm(uint32_t entry) {
    ENTRY(entry)->transfer.w = USB_DEVICE__ENDPOINTX__TRANSFER__ARM_bm;
}

static void wait_interrupt(uint32_t entry, uint32_t mask, uint32_t status) {
    for (uint32_t i = 0; i < TIMEOUT; ++i) {
        if (ENTRY(entry)->interrupt.w & mask) {
            return;
        }
    }
    test_finish(status);
}

/* Packet n of the host carries bytes (n << 4) + i. */
static void check_packet(uint32_t n, uint32_t length, uint32_t status) {
    wait_interrupt(ENTRY_OUT, COMPLETE, status);
    expect(ENTRY(ENTRY_OUT)->interrupt.w == COMPLETE, status + 1u);
    expect(ENTRY(ENTRY_OUT)->status.f.byte_count == length, status + 2u);
    expect(ENTRY(ENTRY_OUT)->status.f.armed == 0, status + 3u);
    for (uint32_t i = 0; i < length; ++i) {
        expect(ram_byte(BASE_OUT + i) == (uint8_t)((n << 4) + i), status + 4u);
    }
    ENTRY(ENTRY_OUT)->interrupt.w = COMPLETE;
}

static void next_phase(void) {
    USB_DEVICE->control.f.enable_pu = 0;
    USB_DEVICE->control.f.enable_pu = 1;
}

int main(void) {
    while (USB_DEVICE->status.f.vbus_present == 0) { }

    configure(ENTRY_OUT, BASE_OUT, 1);
    configure(ENTRY_DOORBELL, BASE_DOORBELL, 2);
    USB_DEVICE->control.f.enable_core = 1;
    arm(ENTRY_OUT);
    USB_DEVICE->control.f.enable_pu = 1;

    check_packet(0xA, 10, 10);
    wait_interrupt(ENTRY_OUT, OVERRUN, 20);
    expect(ENTRY(ENTRY_OUT)->interrupt.w == OVERRUN, 21);
    expect(ENTRY(ENTRY_OUT)->status.f.byte_count == 10, 22);
    ENTRY(ENTRY_OUT)->interrupt.w = OVERRUN;
    arm(ENTRY_OUT);
    next_phase();

    check_packet(0xD, 6, 30);
    arm(ENTRY_OUT);
    arm(ENTRY_DOORBELL);
    ENTRY(ENTRY_OUT)->control.f.stall = 1;
    next_phase();

    wait_interrupt(ENTRY_DOORBELL, COMPLETE, 40);
    expect(ENTRY(ENTRY_OUT)->interrupt.w == 0, 41);
    expect(ENTRY(ENTRY_OUT)->status.f.armed == 1, 42);
    ENTRY(ENTRY_OUT)->control.f.stall = 0;
    next_phase();

    check_packet(0x6, 4, 50);
    arm(ENTRY_OUT);
    ENTRY(ENTRY_OUT)->transfer.w = USB_DEVICE__ENDPOINTX__TRANSFER__RESET_TOGGLE_bm;
    next_phase();

    check_packet(0x8, 3, 60);

    test_finish(1);
    return 0;
}
