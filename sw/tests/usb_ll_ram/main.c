#include <stdint.h>
#include <neorv32.h>
#include "platform.h"
#include "usb_ll.h"
#include "usb_ram.h"
#include "../common/test_status.h"

/*
 * Layer 1 of the USB device library: usb_ll register access and usb_ram copies.
 * Runs on the CPU alone. Prints the copy cost in cycles for a 64-byte packet.
 */

#define BASE ((uintptr_t)USB_DEVICE_BASE)
#define RAM ((volatile uint32_t *)usb_ll_ram(BASE))
#define MAX_LEN 72u
#define GUARD 0xA5A5A5A5u

static uint8_t src_buf[MAX_LEN + 8u] __attribute__((aligned(4)));
static uint8_t dst_buf[MAX_LEN + 8u] __attribute__((aligned(4)));

static void expect(int condition, uint32_t status) {
    if (!condition) {
        test_finish(status);
    }
}

static uint8_t pattern(uint32_t len, uint32_t offset, uint32_t i) {
    return (uint8_t)(len * 7u + offset * 31u + i * 13u + 1u);
}

static uint32_t ram_copy[MAX_LEN / 4u + 2u];

/* The RAM as bytes, read once word by word as the reference for usb_ram. */
static uint8_t ram_byte(uint32_t i) {
    return (uint8_t)(ram_copy[i / 4u] >> (8u * (i % 4u)));
}

/* Every tail length and both loop paths (4-word blocks and single words), then the
   block boundaries up to a full 64-byte packet. */
static const uint8_t lengths[] = { 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17,
                                   31, 32, 33, 47, 48, 49, 63, 64, 65, MAX_LEN };

static void check_copies(void) {
    const uint32_t ram_offset = 64u;
    for (uint32_t n = 0; n < sizeof lengths; ++n) {
        const uint32_t len = lengths[n];
        for (uint32_t offset = 0; offset < 4u; ++offset) {
            const uint32_t words = (len + 3u) / 4u;
            for (uint32_t i = 0; i < words + 1u; ++i) {
                RAM[ram_offset + i] = GUARD;
            }
            for (uint32_t i = 0; i < len; ++i) {
                src_buf[offset + i] = pattern(len, offset, i);
            }
            usb_ram_write(&RAM[ram_offset], &src_buf[offset], len);
            for (uint32_t i = 0; i < words + 1u; ++i) {
                ram_copy[i] = RAM[ram_offset + i];
            }
            for (uint32_t i = 0; i < len; ++i) {
                expect(ram_byte(i) == pattern(len, offset, i), 10);
            }
            expect(ram_copy[words] == GUARD, 11);

            for (uint32_t i = 0; i < sizeof dst_buf; ++i) {
                dst_buf[i] = 0x5Au;
            }
            usb_ram_read(&dst_buf[offset], &RAM[ram_offset], len);
            for (uint32_t i = 0; i < len; ++i) {
                expect(dst_buf[offset + i] == pattern(len, offset, i), 12);
            }
            for (uint32_t i = offset + len; i < sizeof dst_buf; ++i) {
                expect(dst_buf[i] == 0x5Au, 13);
            }
            for (uint32_t i = 0; i < offset; ++i) {
                expect(dst_buf[i] == 0x5Au, 14);
            }
        }
    }
}

static void check_registers(void) {
    expect(usb_ll_core_id(BASE) == 0x600DC0DEu, 20);
    expect(usb_ll_ram_size(BASE) == sizeof(usb_device__usb_ram_t), 21);
    expect(usb_ll_ram(BASE) == USB_RAM_BASE, 22);

    const uint32_t word = usb_ll_ep_control_word(0x123u, 5u, 1u, 3u, USB_LL_KIND_BULK, 1u, 0u);
    usb_ll_ep_control(BASE, 7u, word);
    const usb_device__endpointx__control_t control = { .w = USB_DEVICE->endpoint[7].control.w };
    expect(control.f.base_address == 0x123u && control.f.endpoint_number == 5u && control.f.stall == 1u &&
           control.f.size == 3u && control.f.kind == USB_LL_KIND_BULK && control.f.direction == 1u &&
           control.f.enable == 0u, 23);

    expect(usb_ll_ep_armed(usb_ll_ep_status(BASE, 7u)) == 0u, 24);
    usb_ll_ep_arm(BASE, 7u, 12u);
    expect(usb_ll_ep_armed(usb_ll_ep_status(BASE, 7u)) == 1u, 25);
    usb_ll_ep_reset_toggle(BASE, 7u);
    expect(usb_ll_ep_armed(usb_ll_ep_status(BASE, 7u)) == 1u, 26);

    usb_ll_bus_event_enable(BASE, USB_DEVICE__BUS_EVENT_ENABLE__BUS_RESET_EN_bm);
    /* VBUS rose after reset: the only pending event; taking it clears it. */
    expect((usb_ll_bus_event_take(BASE) & ~USB_DEVICE__BUS_EVENT__VBUS_bm) == 0u, 27);
    expect(usb_ll_bus_event_take(BASE) == 0u, 29);
    expect(usb_ll_ep_irq_take(BASE, 7u) == 0u, 28);
}

static uint32_t cycles(void) {
    return neorv32_cpu_csr_read(CSR_MCYCLE);
}

static void benchmark(void) {
    uint32_t t;

    t = cycles();
    usb_ram_write(&RAM[0], &src_buf[0], 64u);
    const uint32_t write_aligned = cycles() - t;
    t = cycles();
    usb_ram_write(&RAM[0], &src_buf[1], 64u);
    const uint32_t write_unaligned = cycles() - t;
    t = cycles();
    usb_ram_read(&dst_buf[0], &RAM[0], 64u);
    const uint32_t read_aligned = cycles() - t;
    t = cycles();
    usb_ram_read(&dst_buf[1], &RAM[0], 64u);
    const uint32_t read_unaligned = cycles() - t;

    neorv32_uart0_printf("BENCH usb_ram 64 bytes: write %u (unaligned %u), read %u (unaligned %u) cycles\n",
                         write_aligned, write_unaligned, read_aligned, read_unaligned);

    /* Measured 312/789/355/815 cycles without caches; about 25 % margin. */
    expect(write_aligned <= 400u, 30);
    expect(write_unaligned <= 1000u, 31);
    expect(read_aligned <= 450u, 32);
    expect(read_unaligned <= 1000u, 33);
}

int main(void) {
    neorv32_uart0_setup(19200, 0);
    check_registers();
    check_copies();
    benchmark();
    test_finish(1);
    return 0;
}
