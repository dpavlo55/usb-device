/**
 * @file usb_ram.c
 * @brief Layer 1: word-wise copies to and from the shared USB RAM.
 *
 * Without caches every instruction fetch goes over the bus, so loop overhead is as
 * expensive as the RAM access itself: the main loops move four words per iteration.
 * On a little-endian CPU a word-aligned CPU buffer is copied word by word directly;
 * otherwise words are assembled from bytes in registers, so the RAM still only sees
 * word accesses.
 */
#include "usb_ram.h"

#include "usb_common.h"

static inline uint32_t load_le32(const uint8_t *p)
{
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

static inline void store_le32(uint8_t *p, uint32_t w)
{
    p[0] = (uint8_t)w;
    p[1] = (uint8_t)(w >> 8);
    p[2] = (uint8_t)(w >> 16);
    p[3] = (uint8_t)(w >> 24);
}

/* The last 1 to 3 bytes of a buffer, without reading past its end. */
static inline uint32_t load_tail(const uint8_t *p, uint32_t n)
{
    uint32_t w = p[0];
    if (n > 1u) {
        w |= (uint32_t)p[1] << 8;
    }
    if (n > 2u) {
        w |= (uint32_t)p[2] << 16;
    }
    return w;
}

static inline void store_tail(uint8_t *p, uint32_t w, uint32_t n)
{
    p[0] = (uint8_t)w;
    if (n > 1u) {
        p[1] = (uint8_t)(w >> 8);
    }
    if (n > 2u) {
        p[2] = (uint8_t)(w >> 16);
    }
}

void usb_ram_write(volatile uint32_t *dst, const void *src, uint32_t len)
{
    const uint8_t *s = (const uint8_t *)src;
    uint32_t words = len >> 2;

#if USB_CPU_LITTLE_ENDIAN
    if (((uintptr_t)s & 3u) == 0u) {
        const usb_word_t *sw = (const usb_word_t *)s;
        for (; words >= 4u; words -= 4u) {
            const uint32_t w0 = sw[0];
            const uint32_t w1 = sw[1];
            const uint32_t w2 = sw[2];
            const uint32_t w3 = sw[3];
            dst[0] = w0;
            dst[1] = w1;
            dst[2] = w2;
            dst[3] = w3;
            dst += 4;
            sw += 4;
        }
        for (; words > 0u; --words) {
            *dst++ = *sw++;
        }
        s = (const uint8_t *)sw;
    } else
#endif
    {
        for (; words >= 4u; words -= 4u) {
            dst[0] = load_le32(s);
            dst[1] = load_le32(s + 4);
            dst[2] = load_le32(s + 8);
            dst[3] = load_le32(s + 12);
            dst += 4;
            s += 16;
        }
        for (; words > 0u; --words) {
            *dst++ = load_le32(s);
            s += 4;
        }
    }

    if ((len & 3u) != 0u) {
        *dst = load_tail(s, len & 3u);
    }
}

void usb_ram_read(void *dst, const volatile uint32_t *src, uint32_t len)
{
    uint8_t *d = (uint8_t *)dst;
    uint32_t words = len >> 2;

#if USB_CPU_LITTLE_ENDIAN
    if (((uintptr_t)d & 3u) == 0u) {
        usb_word_t *dw = (usb_word_t *)d;
        for (; words >= 4u; words -= 4u) {
            const uint32_t w0 = src[0];
            const uint32_t w1 = src[1];
            const uint32_t w2 = src[2];
            const uint32_t w3 = src[3];
            dw[0] = w0;
            dw[1] = w1;
            dw[2] = w2;
            dw[3] = w3;
            dw += 4;
            src += 4;
        }
        for (; words > 0u; --words) {
            *dw++ = *src++;
        }
        d = (uint8_t *)dw;
    } else
#endif
    {
        for (; words >= 4u; words -= 4u) {
            const uint32_t w0 = src[0];
            const uint32_t w1 = src[1];
            const uint32_t w2 = src[2];
            const uint32_t w3 = src[3];
            store_le32(d, w0);
            store_le32(d + 4, w1);
            store_le32(d + 8, w2);
            store_le32(d + 12, w3);
            d += 16;
            src += 4;
        }
        for (; words > 0u; --words) {
            store_le32(d, *src++);
            d += 4;
        }
    }

    if ((len & 3u) != 0u) {
        store_tail(d, *src, len & 3u);
    }
}
