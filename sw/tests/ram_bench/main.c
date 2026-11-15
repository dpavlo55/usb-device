#include <stdint.h>
#include <neorv32.h>
#include "platform.h"
#include "../common/test_status.h"

/* Measures CPU cycles per access to the USB shared RAM and registers. */

#define N 64u
#define RAM_WORDS ((volatile uint32_t *)(uintptr_t)USB_RAM_BASE)
#define RAM_BYTES ((volatile uint8_t *)(uintptr_t)USB_RAM_BASE)

static uint32_t now(void) {
    return neorv32_cpu_csr_read(CSR_MCYCLE);
}

static void report(const char *what, uint32_t cycles, uint32_t count) {
    neorv32_uart0_printf("BENCH %s: %u cycles for %u accesses = %u.%u per access\n",
                         what, cycles, count, cycles / count, (cycles * 10u / count) % 10u);
}

int main(void) {
    neorv32_uart0_setup(19200, 0);
    uint32_t sink = 0;
    uint32_t t;

    t = now();
    for (uint32_t i = 0; i < N; ++i) { __asm__ volatile ("nop"); }
    const uint32_t loop = now() - t;
    report("empty loop", loop, N);

    t = now();
    for (uint32_t i = 0; i < N; ++i) { RAM_WORDS[i] = i; }
    report("RAM word write", now() - t - loop, N);

    t = now();
    for (uint32_t i = 0; i < N; ++i) { sink += RAM_WORDS[i]; }
    report("RAM word read", now() - t - loop, N);

    t = now();
    for (uint32_t i = 0; i < N; ++i) { RAM_BYTES[i] = (uint8_t)i; }
    report("RAM byte write", now() - t - loop, N);

    t = now();
    for (uint32_t i = 0; i < N; ++i) { sink += RAM_BYTES[i]; }
    report("RAM byte read", now() - t - loop, N);

    t = now();
    for (uint32_t i = 0; i < N; ++i) { sink += USB_DEVICE->interrupt.w; }
    report("register read", now() - t - loop, N);

    t = now();
    for (uint32_t i = 0; i < N; ++i) { USB_DEVICE->bus_event_enable.w = 0; }
    report("register write", now() - t - loop, N);

    static uint32_t dmem[N];
    t = now();
    for (uint32_t i = 0; i < N; ++i) { sink += ((volatile uint32_t *)dmem)[i]; }
    report("DMEM word read", now() - t - loop, N);

    neorv32_uart0_printf("BENCH sink %u\n", sink);
    test_finish(1);
    return 0;
}
