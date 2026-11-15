/**
 * @file main.c
 * @brief Throughput benchmark over CDC-ACM, driven by tools/usb_bench.py.
 *
 * The host sends one command line, then the data phase follows on the same port:
 *
 *   info              -> "INFO <clock_hz> <rx_ring> <tx_ring> <chunk>"
 *   sink <n> <check>  host sends n pattern bytes, the device discards them
 *                     (check = 1: the device compares every byte with the pattern)
 *   source <n>        the device sends n pattern bytes
 *   echo <n>          the device sends back the n bytes it receives
 *   stats             -> "STATS <isr_avg> <isr_max> <h0> .. <h7>" for the last run:
 *                     cycles spent in the USB interrupt (average, maximum) and a
 *                     histogram of the gaps between interrupt entries, in us:
 *                     <10, <25, <50, <100, <250, <500, <1000, >=1000
 *   irq <0|1>         -> "IRQ <0|1>": 0 polls usbd_process() from the loops instead of
 *                     taking the USB interrupt
 *   profile           -> "PROFILE <7 averages>": cycles between the USB_PROFILE points
 *                     of the last run (entry -> 1 -> 2 ... -> 7, see usb_config.h)
 *   ubench            -> "UBENCH <9 cycle counts>": timer overhead, one register
 *                     read, usb_ram_read 64 B, usb_ram_write 64 B, 64 B ring copy
 *                     (byte loop), memcpy 64 B, usbd_process with nothing pending,
 *                     one USB RAM word write, trap entry and return (ecall)
 *
 * After a run the device answers "OK <mode> <n> <cycles> <irqs> <errors>": cycles
 * from the first data byte to the last (mcycle), USB interrupts taken in that time,
 * and pattern mismatches (sink with check only). Pattern byte i is
 * (i + (i >> 8)) & 0xFF, so a lost or repeated 256-byte block shows up as well.
 * Each run is also reported on UART0.
 */
#include <neorv32.h>
#include <string.h>

#include "platform.h"
#include "usb_descriptors.h"
#include "usb_ll.h"
#include "usb_port.h"
#include "usb_port_neorv32.h"
#include "usb_ram.h"
#include "usbd.h"
#include "usbd_cdc_acm.h"

#define CHUNK 1024u

static usbd_t usb;
static usbd_cdc_acm_t cdc;
static volatile uint32_t irq_count;
static volatile bool measuring;
static uint32_t last_entry;
static uint32_t isr_total;
static uint32_t isr_max;
static uint32_t gap_hist[8];
static uint32_t cycles_per_us;
volatile uint32_t usb_profile[8];
volatile uint8_t usb_tracing;
static uint32_t overrun_seen[8];
static uint32_t profile_sum[8];
static uint32_t profile_count[8];
/* pattern_table[r][i] = (i + r) & 0xFF: a run starting at value v is a word-aligned
   copy from row v & 3. */
static uint32_t pattern_table[4][516 / 4];
static uint8_t buffer[CHUNK] __attribute__((aligned(4)));

/* The lean trap entry passes every other trap here: the ecall of the trap
   micro-benchmark returns to the next instruction. */
void usb_port_neorv32_other_trap(uint32_t mcause)
{
    if (mcause == TRAP_CODE_MENV_CALL) {
        neorv32_cpu_csr_write(CSR_MEPC, neorv32_cpu_csr_read(CSR_MEPC) + 4u);
        return;
    }
    for (;;) {
    }
}

static void usb_irq_handler(void)
{
    if (!usb_tracing) {
        ++irq_count;
        usbd_process(&usb);
        return;
    }
    const uint32_t entry = neorv32_cpu_csr_read(CSR_MCYCLE);
    ++irq_count;
    for (uint32_t k = 2u; k < 8u; ++k) {
        if (USB_DEVICE->endpoint[k].interrupt.w & USB_DEVICE__ENDPOINTX__INTERRUPT__OVERRUN_bm) {
            ++overrun_seen[k];
        }
    }
    usb_profile[0] = entry;
    usbd_process(&usb);
    if (measuring) {
        for (uint32_t k = 1u; k < 8u; ++k) {
            if (usb_profile[k] != 0u && usb_profile[k - 1u] != 0u) {
                profile_sum[k] += usb_profile[k] - usb_profile[k - 1u];
                ++profile_count[k];
            }
        }
        const uint32_t spent = neorv32_cpu_csr_read(CSR_MCYCLE) - entry;
        isr_total += spent;
        isr_max = spent > isr_max ? spent : isr_max;
        if (last_entry != 0u) {
            static const uint32_t limits[7] = { 10u, 25u, 50u, 100u, 250u, 500u, 1000u };
            const uint32_t us = (entry - last_entry) / cycles_per_us;
            uint32_t b = 0u;
            while (b < 7u && us >= limits[b]) {
                ++b;
            }
            ++gap_hist[b];
        }
        last_entry = entry;
    }
    for (uint32_t k = 0u; k < 8u; ++k) {
        usb_profile[k] = 0u;
    }
}

static void stats_reset(void)
{
    measuring = false;
    last_entry = 0u;
    isr_total = 0u;
    isr_max = 0u;
    for (uint32_t i = 0u; i < 8u; ++i) {
        gap_hist[i] = 0u;
        profile_sum[i] = 0u;
        profile_count[i] = 0u;
    }
}

static void copy_pattern(uint8_t *dst, uint32_t value, uint32_t n)
{
    const uint8_t *src = (const uint8_t *)pattern_table[value & 3u] + (value & ~3u);
    if (((uintptr_t)dst & 3u) == 0u) {
        uint32_t *d = (uint32_t *)(void *)dst;
        const uint32_t *s = (const uint32_t *)(const void *)src;
        for (; n >= 16u; n -= 16u) {
            const uint32_t w0 = s[0];
            const uint32_t w1 = s[1];
            const uint32_t w2 = s[2];
            const uint32_t w3 = s[3];
            d[0] = w0;
            d[1] = w1;
            d[2] = w2;
            d[3] = w3;
            d += 4;
            s += 4;
        }
        for (; n >= 4u; n -= 4u) {
            *d++ = *s++;
        }
        dst = (uint8_t *)d;
        src = (const uint8_t *)s;
    }
    while (n-- > 0u) {
        *dst++ = *src++;
    }
}

/* Mismatching bytes between data and a run starting at value; word-wise when data
   is aligned, byte by byte only for a run that has a mismatch. */
static uint32_t check_run(const uint8_t *data, uint32_t value, uint32_t n)
{
    const uint8_t *expected = (const uint8_t *)pattern_table[value & 3u] + (value & ~3u);
    uint32_t i = 0u;
    if (((uintptr_t)data & 3u) == 0u) {
        const uint32_t *d = (const uint32_t *)(const void *)data;
        const uint32_t *s = (const uint32_t *)(const void *)expected;
        uint32_t diff = 0u;
        for (; i + 16u <= n; i += 16u) {
            diff |= (d[0] ^ s[0]) | (d[1] ^ s[1]) | (d[2] ^ s[2]) | (d[3] ^ s[3]);
            d += 4;
            s += 4;
        }
        for (; i + 4u <= n; i += 4u) {
            diff |= *d++ ^ *s++;
        }
        if (diff != 0u) {
            i = 0u;
        }
    }
    uint32_t errors = 0u;
    for (; i < n; ++i) {
        errors += data[i] != expected[i];
    }
    return errors;
}

static uint32_t check_pattern(const uint8_t *data, uint32_t position, uint32_t length)
{
    uint32_t errors = 0u;
    while (length > 0u) {
        const uint32_t offset = position & 0xFFu;
        uint32_t n = 256u - offset;
        if (n > length) {
            n = length;
        }
        errors += check_run(data, offset + ((position >> 8) & 0xFFu), n);
        data += n;
        position += n;
        length -= n;
    }
    return errors;
}

static void make_pattern(uint8_t *dst, uint32_t position, uint32_t length)
{
    while (length > 0u) {
        const uint32_t offset = position & 0xFFu;
        uint32_t n = 256u - offset;
        if (n > length) {
            n = length;
        }
        copy_pattern(dst, offset + ((position >> 8) & 0xFFu), n);
        dst += n;
        position += n;
        length -= n;
    }
}

static bool polling;

static bool alive(void)
{
    if (polling) {
        usbd_process(&usb);
    }
    return usbd_cdc_acm_connected(&cdc);
}

static bool write_all(const uint8_t *data, uint32_t length)
{
    for (uint32_t sent = 0u; sent < length;) {
        sent += usbd_cdc_acm_write(&cdc, &data[sent], length - sent);
        if (!alive()) {
            return false;
        }
    }
    return true;
}

static bool tx_drained(void)
{
    return usbd_cdc_acm_write_space(&cdc) == USBD_CDC_ACM_TX_SIZE;
}

static void reply(const char *text)
{
    (void)write_all((const uint8_t *)text, (uint32_t)strlen(text));
}

static uint32_t cycles_now(void)
{
    return neorv32_cpu_csr_read(CSR_MCYCLE);
}

typedef struct {
    uint32_t start;
    uint32_t irqs;
    uint32_t errors;
    bool started;
} run_t;

static void run_start(run_t *run)
{
    if (!run->started) {
        run->started = true;
        run->start = cycles_now();
        run->irqs = irq_count;
        measuring = true;
    }
}

static void finish(const char *mode, uint32_t n, run_t *run)
{
    const uint32_t cycles = run->started ? cycles_now() - run->start : 0u;
    measuring = false;
    const uint32_t irqs = run->started ? irq_count - run->irqs : 0u;
    char line[96];
    /* neorv32 has no snprintf in its tiny libc profile; build the line by hand. */
    char *p = line;
    const char *parts[] = { "OK ", mode, " " };
    for (uint32_t i = 0u; i < 3u; ++i) {
        for (const char *s = parts[i]; *s != '\0'; ++s) {
            *p++ = *s;
        }
    }
    const uint32_t values[] = { n, cycles, irqs, run->errors };
    for (uint32_t i = 0u; i < 4u; ++i) {
        char digits[11];
        uint32_t v = values[i];
        uint32_t d = 0u;
        do {
            digits[d++] = (char)('0' + v % 10u);
            v /= 10u;
        } while (v != 0u);
        while (d > 0u) {
            *p++ = digits[--d];
        }
        *p++ = i < 3u ? ' ' : '\n';
    }
    *p = '\0';
    reply(line);
    neorv32_uart0_printf("%s", line);
}

static char *append_number(char *p, uint32_t v)
{
    char digits[11];
    uint32_t d = 0u;
    *p++ = ' ';
    do {
        digits[d++] = (char)('0' + v % 10u);
        v /= 10u;
    } while (v != 0u);
    while (d > 0u) {
        *p++ = digits[--d];
    }
    return p;
}

static void send_stats(void)
{
    char text[160];
    char *p = text;
    const char *head = "STATS";
    while (*head != '\0') {
        *p++ = *head++;
    }
    const uint32_t irqs = gap_hist[0] + gap_hist[1] + gap_hist[2] + gap_hist[3] + gap_hist[4] + gap_hist[5] +
                          gap_hist[6] + gap_hist[7] + 1u;
    p = append_number(p, isr_total / irqs);
    p = append_number(p, isr_max);
    for (uint32_t i = 0u; i < 8u; ++i) {
        p = append_number(p, gap_hist[i]);
    }
    *p++ = '\n';
    *p = '\0';
    reply(text);
}

static void sink(uint32_t n, bool check)
{
    run_t run = { 0 };
    for (uint32_t got = 0u; got < n;) {
        uint32_t want = n - got;
        if (want > CHUNK) {
            want = CHUNK;
        }
        const uint32_t k = usbd_cdc_acm_read(&cdc, buffer, want);
        if (k == 0u) {
            if (!alive()) {
                return;
            }
            continue;
        }
        run_start(&run);
        if (check) {
            run.errors += check_pattern(buffer, got, k);
        }
        got += k;
    }
    finish("sink", n, &run);
}

static void source(uint32_t n)
{
    run_t run = { 0 };
    run_start(&run);
    for (uint32_t sent = 0u; sent < n;) {
        uint32_t k = n - sent;
        if (k > CHUNK) {
            k = CHUNK;
        }
        make_pattern(buffer, sent, k);
        if (!write_all(buffer, k)) {
            return;
        }
        sent += k;
    }
    while (!tx_drained()) {
        if (!alive()) {
            return;
        }
    }
    finish("source", n, &run);
}

static void echo(uint32_t n)
{
    run_t run = { 0 };
    for (uint32_t done = 0u; done < n;) {
        uint32_t want = n - done;
        if (want > CHUNK) {
            want = CHUNK;
        }
        const uint32_t k = usbd_cdc_acm_read(&cdc, buffer, want);
        if (k == 0u) {
            if (!alive()) {
                return;
            }
            continue;
        }
        run_start(&run);
        if (!write_all(buffer, k)) {
            return;
        }
        done += k;
    }
    while (!tx_drained()) {
        if (!alive()) {
            return;
        }
    }
    finish("echo", n, &run);
}

/* Keeps the compiler from moving or dropping memory accesses around the timer reads. */
#define BARRIER(p) __asm__ volatile("" : : "r"(p) : "memory")

/* Cycle cost of the building blocks of the packet path, measured on idle hardware. */
static void micro_benchmarks(void)
{
    static uint8_t ring[256];
    static volatile uint32_t sink_word;
    const uintptr_t base = USB_DEVICE_BASE;
    volatile uint32_t *ram = (volatile uint32_t *)usb_ll_ram(base);
    volatile uint32_t *spare = ram + (usb_ll_ram_size(base) / 4u) - 16u;
    uint8_t local[64] __attribute__((aligned(4)));
    uint32_t results[9];
    uint32_t t;

    const uint32_t state = usb_port_enter_critical();
    t = cycles_now();
    results[0] = cycles_now() - t;

    t = cycles_now();
    for (uint32_t i = 0u; i < 16u; ++i) {
        sink_word = usb_ll_status(base);
    }
    results[1] = (cycles_now() - t) / 16u;

    t = cycles_now();
    usb_ram_read(local, spare, 64u);
    results[2] = cycles_now() - t;

    t = cycles_now();
    usb_ram_write(spare, local, 64u);
    results[3] = cycles_now() - t;

    t = cycles_now();
    BARRIER(ring);
    uint32_t head = 17u;
    for (uint32_t i = 0u; i < 64u; ++i) {
        ring[head++ & 255u] = local[i];
    }
    BARRIER(ring);
    results[4] = cycles_now() - t;

    t = cycles_now();
    BARRIER(ring);
    memcpy(&ring[64], local, 64u);
    BARRIER(ring);
    results[5] = cycles_now() - t;

    t = cycles_now();
    usbd_process(&usb);
    results[6] = cycles_now() - t;

    t = cycles_now();
    for (uint32_t i = 0u; i < 16u; ++i) {
        spare[i] = i;
    }
    results[7] = (cycles_now() - t) / 16u;

    t = cycles_now();
    __asm__ volatile("ecall" ::: "memory");
    results[8] = cycles_now() - t;
    usb_port_exit_critical(state);

    char text[120];
    char *p = text;
    const char *head_text = "UBENCH";
    while (*head_text != '\0') {
        *p++ = *head_text++;
    }
    for (uint32_t i = 0u; i < 9u; ++i) {
        p = append_number(p, results[i]);
    }
    *p++ = '\n';
    *p = '\0';
    reply(text);
}

static const char *word(const char *s, char *out, uint32_t size)
{
    while (*s == ' ') {
        ++s;
    }
    uint32_t i = 0u;
    while (*s != '\0' && *s != ' ') {
        if (i + 1u < size) {
            out[i++] = *s;
        }
        ++s;
    }
    out[i] = '\0';
    return s;
}

static uint32_t number(const char *s)
{
    uint32_t v = 0u;
    while (*s >= '0' && *s <= '9') {
        v = v * 10u + (uint32_t)(*s++ - '0');
    }
    return v;
}

static void command(const char *line)
{
    char mode[8];
    char arg1[12];
    char arg2[4];
    line = word(line, mode, sizeof mode);
    line = word(line, arg1, sizeof arg1);
    (void)word(line, arg2, sizeof arg2);
    const uint32_t n = number(arg1);
    if (strcmp(mode, "stats") == 0) {
        send_stats();
        return;
    }
    if (strcmp(mode, "nak") == 0) {
        char text[80] = "NAK";
        char *p = &text[3];
        for (uint32_t k = 2u; k < 8u; ++k) {
            p = append_number(p, overrun_seen[k]);
            overrun_seen[k] = 0u;
        }
        *p++ = 10;
        *p = 0;
        reply(text);
        return;
    }
    if (strcmp(mode, "trace") == 0) {
        usb_tracing = (uint8_t)(n != 0u);
        reply(usb_tracing ? "TRACE 1\n" : "TRACE 0\n");
        return;
    }
    if (strcmp(mode, "irq") == 0) {
        polling = n == 0u;
        if (polling) {
            usb_port_irq_disable();
        } else {
            usb_port_irq_enable();
        }
        reply(polling ? "IRQ 0\n" : "IRQ 1\n");
        return;
    }
    if (strcmp(mode, "profile") == 0) {
        char text[120];
        char *p = text;
        const char *head = "PROFILE";
        while (*head != 0) {
            *p++ = *head++;
        }
        for (uint32_t k = 1u; k < 8u; ++k) {
            p = append_number(p, profile_count[k] != 0u ? profile_sum[k] / profile_count[k] : 0u);
        }
        *p++ = 10;
        *p = 0;
        reply(text);
        return;
    }
    if (strcmp(mode, "ubench") == 0) {
        micro_benchmarks();
        return;
    }
    stats_reset();
    if (strcmp(mode, "info") == 0) {
        neorv32_uart0_printf("bench: info\n");
        char text[64];
        char *p = text;
        const uint32_t values[] = { NEORV32_SYSINFO->CLK, USBD_CDC_ACM_RX_SIZE, USBD_CDC_ACM_TX_SIZE, CHUNK };
        const char *head = "INFO";
        while (*head != '\0') {
            *p++ = *head++;
        }
        for (uint32_t i = 0u; i < 4u; ++i) {
            char digits[11];
            uint32_t v = values[i];
            uint32_t d = 0u;
            *p++ = ' ';
            do {
                digits[d++] = (char)('0' + v % 10u);
                v /= 10u;
            } while (v != 0u);
            while (d > 0u) {
                *p++ = digits[--d];
            }
        }
        *p++ = '\n';
        *p = '\0';
        reply(text);
    } else if (strcmp(mode, "sink") == 0) {
        sink(n, number(arg2) != 0u);
    } else if (strcmp(mode, "source") == 0) {
        source(n);
    } else if (strcmp(mode, "echo") == 0) {
        echo(n);
    } else {
        reply("ERR\n");
    }
}

int main(void)
{
    neorv32_rte_setup();
    neorv32_uart0_setup(19200, 0);

    cycles_per_us = NEORV32_SYSINFO->CLK / 1000000u;
    for (uint32_t r = 0u; r < 4u; ++r) {
        uint8_t *row = (uint8_t *)pattern_table[r];
        for (uint32_t i = 0u; i < 516u; ++i) {
            row[i] = (uint8_t)(i + r);
        }
    }

    usbd_cdc_acm_init(&cdc, NULL);
    if (usbd_init(&usb, USB_DEVICE_BASE, &app_descriptors, NULL) != USB_OK ||
        usbd_register_class(&usb, &usbd_cdc_acm_class, &cdc) != USB_OK) {
        neorv32_uart0_printf("bench: init failed\n");
        return 1;
    }
    usb_port_neorv32_fast_irq(usb_irq_handler);
    usb_port_irq_enable();
    neorv32_cpu_csr_set(CSR_MSTATUS, 1u << CSR_MSTATUS_MIE);
    usbd_connect(&usb, true);
    neorv32_uart0_printf("bench: ready\n");

    char line[40];
    uint32_t length = 0u;
    for (;;) {
        if (polling) {
            usbd_process(&usb);
        }
        uint8_t c;
        if (usbd_cdc_acm_read(&cdc, &c, 1u) == 0u) {
            continue;
        }
        if (c == '\n' || c == '\r') {
            line[length] = '\0';
            if (length > 0u) {
                command(line);
            }
            length = 0u;
        } else if (length + 1u < sizeof line) {
            line[length++] = (char)c;
        }
    }
}
