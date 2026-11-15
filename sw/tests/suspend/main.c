#include <stdint.h>
#include "platform.h"
#include "usb_hal.h"
#include "../common/test_status.h"

/*
 * Suspend, remote wakeup and resume through layer 2, paired with
 * tb/cocotb/test_sw_suspend.py. The firmware polls usb_hal_process().
 *   1. After the host's bus reset the bus stays idle: SUSPEND, status.suspended is 1.
 *   2. usb_hal_remote_wakeup(): hardware drives K once the bus has been idle 5 ms; the
 *      host checks the timing and answers with its own resume signaling.
 *   3. RESUME, status.suspended is 0.
 *   4. The bus idles again: SUSPEND; the host resumes on its own: RESUME.
 */

#define MAX_EVENTS 8u

static usb_hal_t hal;
static usb_hal_event_t events[MAX_EVENTS];
static uint32_t event_head;
static uint32_t event_tail;

static void expect(int condition, uint32_t status) {
    if (!condition) {
        test_finish(status);
    }
}

static void on_event(usb_hal_t *h, const usb_hal_event_t *event, void *ctx) {
    (void)h;
    (void)ctx;
    expect(event_head - event_tail < MAX_EVENTS, 2);
    events[event_head++ % MAX_EVENTS] = *event;
}

/* Process until the next event; it must be of @p type. */
static void wait_event(usb_hal_event_type_t type, uint32_t status) {
    for (uint32_t i = 0; event_head == event_tail; ++i) {
        expect(i < 20000000u, status);
        usb_hal_process(&hal);
    }
    expect(events[event_tail++ % MAX_EVENTS].type == type, status + 1u);
}

int main(void) {
    while (USB_DEVICE->status.f.vbus_present == 0) { }

    expect(usb_hal_init(&hal, USB_DEVICE_BASE, on_event, NULL) == USB_OK, 3);
    expect(!usb_hal_suspended(&hal), 4);
    usb_hal_connect(&hal, true);

    wait_event(USB_HAL_EVENT_BUS_RESET, 10);

    wait_event(USB_HAL_EVENT_SUSPEND, 20);
    expect(usb_hal_suspended(&hal), 23);
    usb_hal_remote_wakeup(&hal);
    wait_event(USB_HAL_EVENT_RESUME, 30);
    expect(!usb_hal_suspended(&hal), 33);

    wait_event(USB_HAL_EVENT_SUSPEND, 40);
    wait_event(USB_HAL_EVENT_RESUME, 50);
    expect(!usb_hal_suspended(&hal), 53);
    expect(event_head == event_tail, 54);

    test_finish(1);
    return 0;
}
