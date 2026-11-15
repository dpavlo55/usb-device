/**
 * @file usbd_cdc_acm.c
 * @brief Class driver: CDC-ACM virtual serial port.
 *
 * Ring buffers: the receive ring is filled in usbd_process() and drained by the
 * application, the transmit ring the other way round. Each index has one writer, so
 * the rings need no lock; only starting a transfer from thread context is done in
 * a critical section, because usbd_process() starts transfers too.
 */
#include "usbd_cdc_acm.h"

#include "usb_port.h"
#include "usb_ram.h"

/* Ring contents must be written before the index that publishes them, and read
   after the index that announces them; the compiler may not move them across. */
#if defined(__GNUC__)
#define RING_BARRIER() __asm__ volatile("" ::: "memory")
#elif defined(__STDC_VERSION__) && (__STDC_VERSION__ >= 201112L) && !defined(__STDC_NO_ATOMICS__)
#include <stdatomic.h>
#define RING_BARRIER() atomic_signal_fence(memory_order_seq_cst)
#else
#define RING_BARRIER()
#endif

#define CDC_MAX_PACKET 64u

#define CDC_CLASS_COMMUNICATION 0x02u
#define CDC_SUBCLASS_ACM 0x02u
#define CDC_CLASS_DATA 0x0Au

#define CDC_REQ_SET_LINE_CODING 0x20u
#define CDC_REQ_GET_LINE_CODING 0x21u
#define CDC_REQ_SET_CONTROL_LINE_STATE 0x22u
#define CDC_REQ_SEND_BREAK 0x23u

_Static_assert((USBD_CDC_ACM_RX_SIZE & (USBD_CDC_ACM_RX_SIZE - 1u)) == 0u, "RX ring size must be a power of two");
_Static_assert((USBD_CDC_ACM_TX_SIZE & (USBD_CDC_ACM_TX_SIZE - 1u)) == 0u, "TX ring size must be a power of two");
_Static_assert(USBD_CDC_ACM_RX_SIZE >= CDC_MAX_PACKET && USBD_CDC_ACM_TX_SIZE >= CDC_MAX_PACKET,
               "rings must hold a full packet");
_Static_assert(USBD_CDC_ACM_BUFFERS == 1u || USBD_CDC_ACM_BUFFERS == 2u, "one or two buffers per endpoint");
_Static_assert(USBD_CDC_ACM_IN_TRANSFER <= USBD_CDC_ACM_TX_SIZE && USBD_CDC_ACM_IN_TRANSFER <= 1024u &&
               (USBD_CDC_ACM_IN_TRANSFER & (USBD_CDC_ACM_IN_TRANSFER - 1u)) == 0u,
               "IN transfer: a power of two that fits the transmit ring");

static void encode_line_coding(uint8_t raw[7], const usbd_cdc_acm_line_coding_t *c)
{
    raw[0] = (uint8_t)c->baud_rate;
    raw[1] = (uint8_t)(c->baud_rate >> 8);
    raw[2] = (uint8_t)(c->baud_rate >> 16);
    raw[3] = (uint8_t)(c->baud_rate >> 24);
    raw[4] = c->stop_bits;
    raw[5] = c->parity;
    raw[6] = c->data_bits;
}

static void decode_line_coding(usbd_cdc_acm_line_coding_t *c, const uint8_t raw[7])
{
    c->baud_rate = (uint32_t)raw[0] | ((uint32_t)raw[1] << 8) | ((uint32_t)raw[2] << 16) | ((uint32_t)raw[3] << 24);
    c->stop_bits = raw[4];
    c->parity = raw[5];
    c->data_bits = raw[6];
}

static uint32_t rx_space(const usbd_cdc_acm_t *cdc)
{
    return USBD_CDC_ACM_RX_SIZE - (cdc->rx_head - cdc->rx_tail);
}

/* Copy n bytes between a CPU buffer and a ring piece. The destination is brought
   to a word boundary first; then whole words are copied, shifted together from two
   source words when the source is at another byte offset (reading at most the
   source word that holds its last byte). */
static void copy_bytes(uint8_t *dst, const uint8_t *src, uint32_t n)
{
    while (n > 0u && ((uintptr_t)dst & 3u) != 0u) {
        *dst++ = *src++;
        --n;
    }
    uint32_t *d = (uint32_t *)(void *)dst;
    const uint32_t offset = (uintptr_t)src & 3u;
    if (offset == 0u) {
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
        src = (const uint8_t *)s;
    } else if (n >= 4u) {
        const uint32_t shift = 8u * offset;
        const uint32_t *s = (const uint32_t *)(const void *)(src - offset);
        uint32_t current = *s++;
        for (; n >= 4u; n -= 4u) {
            const uint32_t next = *s++;
            *d++ = (current >> shift) | (next << (32u - shift));
            current = next;
        }
        src = (const uint8_t *)s - 4u + offset;
    }
    dst = (uint8_t *)d;
    while (n-- > 0u) {
        *dst++ = *src++;
    }
}

static void ring_put(uint8_t *ring, uint32_t size, uint32_t pos, const uint8_t *src, uint32_t n)
{
    pos &= size - 1u;
    const uint32_t first = n < size - pos ? n : size - pos;
    copy_bytes(&ring[pos], src, first);
    copy_bytes(ring, src + first, n - first);
}

static void ring_get(uint8_t *dst, const uint8_t *ring, uint32_t size, uint32_t pos, uint32_t n)
{
    pos &= size - 1u;
    const uint32_t first = n < size - pos ? n : size - pos;
    copy_bytes(dst, &ring[pos], first);
    copy_bytes(dst + first, ring, n - first);
}

/* A received packet straight from the endpoint's buffer in the USB RAM into the
   receive ring at any ring position. Only whole words are read from the USB RAM
   (never past the packet's last word) and, apart from up to 3 bytes at each end,
   only whole words are written to the ring: with the ring index at byte d of a
   word, each ring word is the top 4 - d bytes of one RAM word and the low d bytes
   of the next. The ring ends on a word boundary, so wrapping keeps that scheme. */
static void ring_from_ram(usbd_cdc_acm_t *cdc, const volatile uint32_t *src, uint32_t n)
{
    const uint32_t words = USBD_CDC_ACM_RX_SIZE / 4u;
    uint8_t *ring = cdc->rx_ring;
    uint32_t *ring_words = (uint32_t *)(void *)ring;
    const uint32_t pos = cdc->rx_head & (USBD_CDC_ACM_RX_SIZE - 1u);
    const uint32_t d = pos & 3u;

    if (d == 0u) {
        const uint32_t first = n < USBD_CDC_ACM_RX_SIZE - pos ? n : USBD_CDC_ACM_RX_SIZE - pos;
        usb_ram_read(&ring[pos], src, first);
        if (n > first) {
            usb_ram_read(ring, src + first / 4u, n - first);
        }
        return;
    }

    const uint32_t lead = 4u - d;
    uint32_t w = *src++;
    if (n <= lead) {
        for (uint32_t j = 0u; j < n; ++j) {
            ring[pos + j] = (uint8_t)(w >> (8u * j));
        }
        return;
    }
    for (uint32_t j = 0u; j < lead; ++j) {
        ring[pos + j] = (uint8_t)(w >> (8u * j));
    }
    const uint32_t shift = 8u * d;
    uint32_t carry = w >> (32u - shift);
    uint32_t index = ((pos + lead) & (USBD_CDC_ACM_RX_SIZE - 1u)) / 4u;
    uint32_t remaining = n - lead;
    for (; remaining >= 4u; remaining -= 4u) {
        const uint32_t next = *src++;
        ring_words[index] = carry | (next << shift);
        index = (index + 1u) & (words - 1u);
        carry = next >> (32u - shift);
    }
    if (remaining > 0u) {
        uint32_t tail = carry;
        if (remaining > d) {
            tail |= *src << shift;
        }
        uint8_t *t = &ring[index * 4u];
        for (uint32_t j = 0u; j < remaining; ++j) {
            t[j] = (uint8_t)(tail >> (8u * j));
        }
    }
}

/* The next n bytes of the transmit ring straight into the endpoint's buffer, as
   whole words on both sides: with the ring index at byte d of a word, each RAM word
   is the top 4 - d bytes of one ring word and the low d bytes of the next. Bytes
   past n in the last RAM word are don't-care (only n bytes are sent). */
static void ring_to_ram(usbd_cdc_acm_t *cdc, volatile uint32_t *dst, uint32_t n)
{
    const uint32_t words = USBD_CDC_ACM_TX_SIZE / 4u;
    const uint32_t *ring_words = (const uint32_t *)(const void *)cdc->tx_ring;
    const uint32_t pos = cdc->tx_tail & (USBD_CDC_ACM_TX_SIZE - 1u);
    const uint32_t d = pos & 3u;
    uint32_t index = pos / 4u;
    uint32_t count = (n + 3u) / 4u;

    if (d == 0u) {
        while (count > 0u) {
            uint32_t run = words - index < count ? words - index : count;
            const uint32_t *s = &ring_words[index];
            count -= run;
            index = 0u;
            for (; run >= 4u; run -= 4u) {
                const uint32_t w0 = s[0];
                const uint32_t w1 = s[1];
                const uint32_t w2 = s[2];
                const uint32_t w3 = s[3];
                dst[0] = w0;
                dst[1] = w1;
                dst[2] = w2;
                dst[3] = w3;
                s += 4;
                dst += 4;
            }
            while (run-- > 0u) {
                *dst++ = *s++;
            }
        }
        return;
    }
    const uint32_t shift = 8u * d;
    const uint32_t back = 32u - shift;
    uint32_t current = ring_words[index];
    index = (index + 1u) & (words - 1u);
    while (count > 0u) {
        uint32_t run = words - index < count ? words - index : count;
        const uint32_t *s = &ring_words[index];
        count -= run;
        index = 0u;
        for (; run >= 4u; run -= 4u) {
            const uint32_t w0 = s[0];
            const uint32_t w1 = s[1];
            const uint32_t w2 = s[2];
            const uint32_t w3 = s[3];
            dst[0] = (current >> shift) | (w0 << back);
            dst[1] = (w0 >> shift) | (w1 << back);
            dst[2] = (w1 >> shift) | (w2 << back);
            dst[3] = (w2 >> shift) | (w3 << back);
            current = w3;
            s += 4;
            dst += 4;
        }
        while (run-- > 0u) {
            const uint32_t next = *s++;
            *dst++ = (current >> shift) | (next << back);
            current = next;
        }
    }
}

/* True when the receive ring has room for one more armed packet on top of the
   packets already armed. */
static bool rx_room(const usbd_cdc_acm_t *cdc)
{
    return cdc->rx_armed < USBD_CDC_ACM_BUFFERS &&
           rx_space(cdc) >= (cdc->rx_armed + 1u) * (uint32_t)cdc->max_packet;
}

/* Arm every free OUT buffer the receive ring has room for. */
static void receive(usbd_cdc_acm_t *cdc)
{
    while (cdc->configured && rx_room(cdc) &&
           usbd_ep_arm_raw(cdc->dev, cdc->ep_out, cdc->max_packet) == USB_OK) {
        ++cdc->rx_armed;
    }
}

/* Fill every free IN buffer from the transmit ring; a ZLP follows a full last
   packet once nothing else is in flight. */
static void transmit(usbd_cdc_acm_t *cdc)
{
    while (cdc->configured && cdc->tx_armed < USBD_CDC_ACM_BUFFERS) {
        const uint32_t queued = cdc->tx_head - cdc->tx_tail;
        if (queued == 0u && !(cdc->tx_last_full && cdc->tx_armed == 0u)) {
            return;
        }
        const uint32_t n = queued < cdc->in_transfer ? queued : cdc->in_transfer;
        RING_BARRIER();
        if (n > 0u) {
            ring_to_ram(cdc, usbd_ep_buffer(cdc->dev, cdc->ep_in), n);
        }
        RING_BARRIER();
        cdc->tx_tail += n;
        cdc->tx_last_full = (uint8_t)(n != 0u && (n & (cdc->max_packet - 1u)) == 0u);
        if (usbd_ep_arm_raw(cdc->dev, cdc->ep_in, n) != USB_OK) {
            return;
        }
        ++cdc->tx_armed;
    }
}

/* ---- Class driver ------------------------------------------------------------ */

static void cdc_reset(usbd_t *dev, void *ctx)
{
    (void)dev;
    usbd_cdc_acm_t *cdc = (usbd_cdc_acm_t *)ctx;
    cdc->configured = 0u;
    cdc->tx_armed = 0u;
    cdc->rx_armed = 0u;
    cdc->tx_last_full = 0u;
    cdc->line_state = 0u;
    cdc->rx_head = cdc->rx_tail = 0u;
    cdc->tx_head = cdc->tx_tail = 0u;
}

static uint16_t cdc_open(usbd_t *dev, void *ctx, const uint8_t *desc, uint16_t length)
{
    usbd_cdc_acm_t *cdc = (usbd_cdc_acm_t *)ctx;
    uint16_t pos = 0u;

    if (length >= USB_DESC_IAD_LEN && desc[1] == USB_DESC_TYPE_INTERFACE_ASSOCIATION) {
        if (desc[4] != CDC_CLASS_COMMUNICATION || desc[5] != CDC_SUBCLASS_ACM) {
            return 0u;
        }
        pos = desc[0];
    }
    if (pos + USB_DESC_INTERFACE_LEN > length || desc[pos + 1u] != USB_DESC_TYPE_INTERFACE ||
        desc[pos + 5u] != CDC_CLASS_COMMUNICATION || desc[pos + 6u] != CDC_SUBCLASS_ACM) {
        return 0u;
    }

    const uint8_t communication = desc[pos + 2u];
    bool data_interface = false;
    uint8_t ep_out = 0u;
    uint8_t ep_in = 0u;
    uint16_t max_packet = 0u;
    pos = (uint16_t)(pos + desc[pos]);

    while (pos + 2u <= length && desc[pos] != 0u) {
        const uint8_t type = desc[pos + 1u];
        if (type == USB_DESC_TYPE_INTERFACE || type == USB_DESC_TYPE_INTERFACE_ASSOCIATION) {
            const bool ours = type == USB_DESC_TYPE_INTERFACE && !data_interface &&
                              desc[pos + 2u] == (uint8_t)(communication + 1u) && desc[pos + 5u] == CDC_CLASS_DATA;
            if (!ours) {
                break;
            }
            data_interface = true;
        } else if (type == USB_DESC_TYPE_ENDPOINT) {
            const uint8_t ep_addr = desc[pos + 2u];
            const usb_status_t opened = !data_interface ? usbd_ep_open(dev, &desc[pos])
                                        : usbd_ep_open_ex(dev, &desc[pos],
                                                          USB_EP_IS_IN(ep_addr) ? USBD_CDC_ACM_IN_TRANSFER : 0u,
                                                          USBD_CDC_ACM_BUFFERS);
            if (opened != USB_OK) {
                return 0u;
            }
            if (!data_interface) {
                cdc->ep_notify = ep_addr;
            } else if (USB_EP_IS_IN(ep_addr)) {
                ep_in = ep_addr;
            } else {
                ep_out = ep_addr;
            }
            const uint16_t packet = (uint16_t)((desc[pos + 4u] | ((uint16_t)desc[pos + 5u] << 8)) & 0x07FFu);
            if (data_interface && packet > max_packet) {
                max_packet = packet;
            }
        }
        pos = (uint16_t)(pos + desc[pos]);
    }

    if (!data_interface || ep_out == 0u || ep_in == 0u || max_packet == 0u || max_packet > CDC_MAX_PACKET) {
        return 0u;
    }
    cdc_reset(dev, ctx);
    cdc->dev = dev;
    cdc->interface = communication;
    cdc->ep_out = ep_out;
    cdc->ep_in = ep_in;
    cdc->max_packet = max_packet;
    cdc->in_transfer = USBD_CDC_ACM_IN_TRANSFER > max_packet ? USBD_CDC_ACM_IN_TRANSFER : max_packet;
    cdc->configured = 1u;
    receive(cdc);
    return pos;
}

static bool cdc_control(usbd_t *dev, void *ctx, usbd_ctrl_stage_t stage, const usb_setup_t *setup)
{
    usbd_cdc_acm_t *cdc = (usbd_cdc_acm_t *)ctx;
    if ((setup->bmRequestType & USB_REQ_TYPE_MASK) != USB_REQ_TYPE_CLASS) {
        return false;
    }
    switch (setup->bRequest) {
    case CDC_REQ_SET_LINE_CODING:
        if (stage == USBD_CTRL_STAGE_SETUP) {
            usbd_ctrl_receive(dev, cdc->line_coding_raw, sizeof cdc->line_coding_raw);
            return setup->wLength == sizeof cdc->line_coding_raw;
        }
        decode_line_coding(&cdc->line_coding, cdc->line_coding_raw);
        if (cdc->callbacks.line_coding_changed != NULL) {
            cdc->callbacks.line_coding_changed(cdc, &cdc->line_coding, cdc->callbacks.ctx);
        }
        return true;
    case CDC_REQ_GET_LINE_CODING:
        encode_line_coding(cdc->line_coding_raw, &cdc->line_coding);
        usbd_ctrl_send(dev, cdc->line_coding_raw, sizeof cdc->line_coding_raw);
        return true;
    case CDC_REQ_SET_CONTROL_LINE_STATE:
        cdc->line_state = (uint8_t)(setup->wValue & 0x03u);
        if (cdc->callbacks.line_state_changed != NULL) {
            cdc->callbacks.line_state_changed(cdc, (cdc->line_state & 1u) != 0u, (cdc->line_state & 2u) != 0u,
                                              cdc->callbacks.ctx);
        }
        return true;
    case CDC_REQ_SEND_BREAK:
        return true;
    default:
        return false;
    }
}

static void cdc_xfer_done(usbd_t *dev, void *ctx, uint8_t ep_addr, uint32_t length)
{
    USB_PROFILE(4);
    usbd_cdc_acm_t *cdc = (usbd_cdc_acm_t *)ctx;
    if (ep_addr == cdc->ep_out) {
        --cdc->rx_armed;
        if (length > 0u) {
            ring_from_ram(cdc, usbd_ep_done_buffer(dev, cdc->ep_out), length);
        }
        RING_BARRIER();
        cdc->rx_head += length;
        USB_PROFILE(5);
        receive(cdc);
        USB_PROFILE(6);
        if (length > 0u && cdc->callbacks.received != NULL) {
            cdc->callbacks.received(cdc, cdc->callbacks.ctx);
        }
    } else if (ep_addr == cdc->ep_in) {
        --cdc->tx_armed;
        USB_PROFILE(5);
        transmit(cdc);
        USB_PROFILE(6);
    }
}

const usbd_class_t usbd_cdc_acm_class = {
    .name = "cdc_acm",
    .reset = cdc_reset,
    .open = cdc_open,
    .control = cdc_control,
    .xfer_done = cdc_xfer_done,
};

/* ---- Application API ---------------------------------------------------------- */

void usbd_cdc_acm_init(usbd_cdc_acm_t *cdc, const usbd_cdc_acm_callbacks_t *callbacks)
{
    *cdc = (usbd_cdc_acm_t){ 0 };
    if (callbacks != NULL) {
        cdc->callbacks = *callbacks;
    }
    cdc->line_coding = (usbd_cdc_acm_line_coding_t){ .baud_rate = 115200u, .stop_bits = 0u, .parity = 0u, .data_bits = 8u };
}

uint32_t usbd_cdc_acm_write(usbd_cdc_acm_t *cdc, const void *data, uint32_t length)
{
    const uint32_t space = usbd_cdc_acm_write_space(cdc);
    const uint32_t n = length < space ? length : space;
    RING_BARRIER();
    ring_put(cdc->tx_ring, USBD_CDC_ACM_TX_SIZE, cdc->tx_head, (const uint8_t *)data, n);
    RING_BARRIER();
    cdc->tx_head += n;
    if (n > 0u) {
        const uint32_t state = usb_port_enter_critical();
        transmit(cdc);
        usb_port_exit_critical(state);
    }
    return n;
}

uint32_t usbd_cdc_acm_read(usbd_cdc_acm_t *cdc, void *data, uint32_t length)
{
    const uint32_t available = usbd_cdc_acm_available(cdc);
    const uint32_t n = length < available ? length : available;
    RING_BARRIER();
    ring_get((uint8_t *)data, cdc->rx_ring, USBD_CDC_ACM_RX_SIZE, cdc->rx_tail, n);
    RING_BARRIER();
    cdc->rx_tail += n;
    if (rx_room(cdc)) {
        const uint32_t state = usb_port_enter_critical();
        receive(cdc);
        usb_port_exit_critical(state);
    }
    return n;
}

uint32_t usbd_cdc_acm_available(const usbd_cdc_acm_t *cdc)
{
    return cdc->rx_head - cdc->rx_tail;
}

uint32_t usbd_cdc_acm_write_space(const usbd_cdc_acm_t *cdc)
{
    return USBD_CDC_ACM_TX_SIZE - (cdc->tx_head - cdc->tx_tail);
}

bool usbd_cdc_acm_connected(const usbd_cdc_acm_t *cdc)
{
    return cdc->configured && (cdc->line_state & 1u) != 0u;
}

usbd_cdc_acm_line_coding_t usbd_cdc_acm_line_coding(const usbd_cdc_acm_t *cdc)
{
    return cdc->line_coding;
}
