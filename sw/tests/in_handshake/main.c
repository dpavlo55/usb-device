#include <stdint.h>
#include "platform.h"
#include "../common/test_status.h"

/*
 * Paired with tb/cocotb/test_sw_in_handshake.py. Entry 3 is BULK IN on endpoint
 * 1, entry 2 is BULK OUT on endpoint 2 (a doorbell). Each phase ends with a
 * pull-up toggle that lets the host start the next one; the host checks the data
 * and handshakes, the firmware checks the events and the arm state. The host's
 * last action in a phase is the one the firmware waits for.
 *   1. P1 (8 bytes) armed: sent as DATA0 and ACKed, transfer complete. The next
 *      IN finds the entry unarmed: NAK. Then the doorbell.
 *   2. P2 (5 bytes) armed: the host does not ACK the first DATA1, so the retry
 *      sends the same DATA1; the second one is ACKed.
 *   3. Zero-length armed: DATA0 with no payload, ACKed.
 *   4. P4 armed and stalled: STALL, then the doorbell; no event on entry 3,
 *      which stays armed. Stall cleared and the toggle reset.
 *   5. The same P4 goes out as DATA0 and is ACKed.
 */

#define TIMEOUT 400000u
#define ENTRY_IN 3u
#define ENTRY_DOORBELL 2u
#define BASE_IN 0x40u
#define BASE_DOORBELL 0x80u

#define ENTRY(k) (&USB_DEVICE->endpoint[(k)])
#define INTERRUPT_ALL (USB_DEVICE__ENDPOINTX__INTERRUPT__SETUP_bm | \
                       USB_DEVICE__ENDPOINTX__INTERRUPT__CRC_ERROR_bm | \
                       USB_DEVICE__ENDPOINTX__INTERRUPT__TRANSFER_COMPLETE_bm | \
                       USB_DEVICE__ENDPOINTX__INTERRUPT__ZLP_bm | \
                       USB_DEVICE__ENDPOINTX__INTERRUPT__OVERRUN_bm)
#define COMPLETE USB_DEVICE__ENDPOINTX__INTERRUPT__TRANSFER_COMPLETE_bm

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

/* Packet n carries bytes (n << 4) + i. */
static void arm_in(uint32_t n, uint32_t length) {
    for (uint32_t i = 0; i < length; ++i) {
        ram_write(BASE_IN + i, (uint8_t)((n << 4) + i));
    }
    ENTRY(ENTRY_IN)->transfer.w = USB_DEVICE__ENDPOINTX__TRANSFER__ARM_bm |
                                  (length << USB_DEVICE__ENDPOINTX__TRANSFER__LENGTH_bp);
}

static void wait_complete(uint32_t entry, uint32_t status) {
    for (uint32_t i = 0; i < TIMEOUT; ++i) {
        if (ENTRY(entry)->interrupt.w & COMPLETE) {
            expect(ENTRY(entry)->interrupt.w == COMPLETE, status + 1u);
            expect(ENTRY(entry)->status.f.armed == 0, status + 2u);
            ENTRY(entry)->interrupt.w = COMPLETE;
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

    configure(ENTRY_IN, BASE_IN, 1, 0);
    configure(ENTRY_DOORBELL, BASE_DOORBELL, 2, 1);
    USB_DEVICE->control.f.enable_core = 1;
    arm_in(0x1, 8);
    ENTRY(ENTRY_DOORBELL)->transfer.w = USB_DEVICE__ENDPOINTX__TRANSFER__ARM_bm;
    USB_DEVICE->control.f.enable_pu = 1;

    wait_complete(ENTRY_IN, 10);
    wait_complete(ENTRY_DOORBELL, 14);
    arm_in(0x2, 5);
    next_phase();

    wait_complete(ENTRY_IN, 20);
    arm_in(0x3, 0);
    next_phase();

    wait_complete(ENTRY_IN, 30);
    arm_in(0x4, 4);
    ENTRY(ENTRY_DOORBELL)->transfer.w = USB_DEVICE__ENDPOINTX__TRANSFER__ARM_bm;
    ENTRY(ENTRY_IN)->control.f.stall = 1;
    next_phase();

    wait_complete(ENTRY_DOORBELL, 40);
    expect(ENTRY(ENTRY_IN)->interrupt.w == 0, 44);
    expect(ENTRY(ENTRY_IN)->status.f.armed == 1, 45);
    ENTRY(ENTRY_IN)->control.f.stall = 0;
    ENTRY(ENTRY_IN)->transfer.w = USB_DEVICE__ENDPOINTX__TRANSFER__RESET_TOGGLE_bm;
    next_phase();

    wait_complete(ENTRY_IN, 50);

    test_finish(1);
    return 0;
}
