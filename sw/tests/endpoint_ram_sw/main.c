#include <stdint.h>
#include "platform.h"
#include "../common/test_status.h"

/* Favor throughput over code size for the endpoint RAM access loops. */
__attribute__((optimize("O2"))) int main(void) {
    volatile uint32_t *const ram = (volatile uint32_t *)(uintptr_t)USB_RAM_BASE;

    /* Attach the device so USB packet tests can run with this firmware. */
    while (USB_DEVICE->status.f.vbus_present == 0) { }
    USB_DEVICE->control.f.enable_pu = 1;

    const uint32_t size_bytes = USB_DEVICE->status.f.ram_words * 4u;
    const uint32_t words = size_bytes / sizeof(uint32_t);
    uint32_t failure = 0;

    if (size_bytes == 0 || size_bytes % sizeof(uint32_t) != 0) {
        test_finish(UINT32_MAX);
    }

    volatile uint32_t *cursor = ram;
    #pragma GCC unroll 4
    for (uint32_t i = 0; i < words; ++i) {
        *cursor++ = i;
    }

    cursor = ram;
    #pragma GCC unroll 4
    for (uint32_t i = 0; i < words; ++i) {
        if (*cursor++ != i && failure == 0) {
            failure = i + 2u; /* Identify the first failing word. */
        }
    }

    test_finish(failure ? failure : 1u);
}
