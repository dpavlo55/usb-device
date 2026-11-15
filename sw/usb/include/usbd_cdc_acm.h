/**
 * @file usbd_cdc_acm.h
 * @brief Class driver: CDC-ACM virtual serial port (USB CDC 1.2, PSTN 1.2).
 *
 * Hosts bind their own driver to it (usbser.sys on Windows 10 and later,
 * cdc_acm on Linux, AppleUSBCDC on macOS), so the device appears as a COM port
 * that any terminal or pyserial can open. The baud rate set by the host is only
 * information; data always moves at USB speed.
 *
 * Data moves through two ring buffers. usbd_cdc_acm_write() and usbd_cdc_acm_read()
 * may be called from thread context while usbd_process() runs in the interrupt.
 * When the receive ring is full, the OUT endpoint is left unarmed and the core NAKs
 * the host until the application reads: flow control without lost data.
 */
#ifndef USBD_CDC_ACM_H
#define USBD_CDC_ACM_H

#include "usbd.h"

#ifdef __cplusplus
extern "C" {
#endif

/** Ring buffer sizes, powers of two. */
#ifndef USBD_CDC_ACM_RX_SIZE
#define USBD_CDC_ACM_RX_SIZE 256u
#endif
#ifndef USBD_CDC_ACM_TX_SIZE
#define USBD_CDC_ACM_TX_SIZE 256u
#endif

/** Hardware buffers per data endpoint: 2 lets the host move the next packet while
    the previous one is copied (usbd_ep_open_double()), 1 uses one entry each. */
#ifndef USBD_CDC_ACM_BUFFERS
#define USBD_CDC_ACM_BUFFERS 2u
#endif

/** Bytes per IN transfer (a power of two, at most USBD_CDC_ACM_TX_SIZE and 1024):
    hardware sends them as full packets with one interrupt per transfer instead of
    one per packet. The max packet size sends one packet per transfer. */
#ifndef USBD_CDC_ACM_IN_TRANSFER
#define USBD_CDC_ACM_IN_TRANSFER 256u
#endif

/** Length of USBD_CDC_ACM_DESCRIPTOR(). */
#define USBD_CDC_ACM_DESC_LEN 66u

/**
 * Descriptors of one CDC-ACM function for a configuration descriptor: an interface
 * association, the communication interface @p itf with its notification endpoint
 * @p ep_notify (interrupt IN), and the data interface @p itf + 1 with bulk endpoints
 * @p ep_out and @p ep_in of @p max_packet bytes. @p i_string names the function.
 */
#define USBD_CDC_ACM_DESCRIPTOR(itf, i_string, ep_notify, ep_out, ep_in, max_packet)  \
    USB_DESC_IAD((itf), 2u, 0x02u, 0x02u, 0x00u, 0u),                                 \
    USB_DESC_INTERFACE((itf), 0u, 1u, 0x02u, 0x02u, 0x00u, (i_string)),               \
    5u, USB_DESC_TYPE_CS_INTERFACE, 0x00u, USB_U16(0x0110u),                          \
    5u, USB_DESC_TYPE_CS_INTERFACE, 0x01u, 0x00u, (uint8_t)((itf) + 1u),              \
    4u, USB_DESC_TYPE_CS_INTERFACE, 0x02u, 0x02u,                                     \
    5u, USB_DESC_TYPE_CS_INTERFACE, 0x06u, (itf), (uint8_t)((itf) + 1u),              \
    USB_DESC_ENDPOINT((ep_notify), USB_EP_INTERRUPT, 8u, 16u),                        \
    USB_DESC_INTERFACE((uint8_t)((itf) + 1u), 0u, 2u, 0x0Au, 0x00u, 0x00u, 0u),       \
    USB_DESC_ENDPOINT((ep_out), USB_EP_BULK, (max_packet), 0u),                       \
    USB_DESC_ENDPOINT((ep_in), USB_EP_BULK, (max_packet), 0u)

/** Line coding (CDC PSTN 6.3.11). */
typedef struct {
    uint32_t baud_rate;
    uint8_t stop_bits;   /**< 0: 1, 1: 1.5, 2: 2 */
    uint8_t parity;      /**< 0: none, 1: odd, 2: even, 3: mark, 4: space */
    uint8_t data_bits;   /**< 5, 6, 7, 8 or 16 */
} usbd_cdc_acm_line_coding_t;

typedef struct usbd_cdc_acm usbd_cdc_acm_t;

/** Callbacks; any may be NULL. They run in the context of usbd_process(). */
typedef struct {
    /** Data arrived in the receive ring. */
    void (*received)(usbd_cdc_acm_t *cdc, void *ctx);
    /** The host changed DTR or RTS (a terminal opening the port sets DTR). */
    void (*line_state_changed)(usbd_cdc_acm_t *cdc, bool dtr, bool rts, void *ctx);
    /** The host set a new line coding. */
    void (*line_coding_changed)(usbd_cdc_acm_t *cdc, const usbd_cdc_acm_line_coding_t *coding, void *ctx);
    void *ctx;
} usbd_cdc_acm_callbacks_t;

/** @cond INTERNAL */
struct usbd_cdc_acm {
    usbd_cdc_acm_callbacks_t callbacks;
    usbd_t *dev;
    uint8_t interface;
    uint8_t ep_notify;
    uint8_t ep_out;
    uint8_t ep_in;
    uint16_t max_packet;
    uint16_t in_transfer;
    volatile uint8_t configured;
    volatile uint8_t rx_armed;    /* OUT buffers armed */
    volatile uint8_t tx_armed;    /* IN buffers armed */
    uint8_t tx_last_full;
    uint8_t line_state;
    usbd_cdc_acm_line_coding_t line_coding;
    uint8_t line_coding_raw[7];
    volatile uint32_t rx_head;
    volatile uint32_t rx_tail;
    volatile uint32_t tx_head;
    volatile uint32_t tx_tail;
    uint8_t rx_ring[USBD_CDC_ACM_RX_SIZE] USB_ALIGNED(4);
    uint8_t tx_ring[USBD_CDC_ACM_TX_SIZE] USB_ALIGNED(4);
};
/** @endcond */

/** The class driver; register it with usbd_register_class(dev, &usbd_cdc_acm_class, cdc). */
extern const usbd_class_t usbd_cdc_acm_class;

/** Prepare an instance; @p callbacks may be NULL. */
void usbd_cdc_acm_init(usbd_cdc_acm_t *cdc, const usbd_cdc_acm_callbacks_t *callbacks);

/** Queue up to @p length bytes for the host; returns how many fit in the transmit ring. */
uint32_t usbd_cdc_acm_write(usbd_cdc_acm_t *cdc, const void *data, uint32_t length);

/** Take up to @p length received bytes; returns how many were taken. */
uint32_t usbd_cdc_acm_read(usbd_cdc_acm_t *cdc, void *data, uint32_t length);

/** Received bytes waiting to be read. */
uint32_t usbd_cdc_acm_available(const usbd_cdc_acm_t *cdc);

/** Free space in the transmit ring. */
uint32_t usbd_cdc_acm_write_space(const usbd_cdc_acm_t *cdc);

/** True while configured and the host holds DTR (a terminal has the port open). */
bool usbd_cdc_acm_connected(const usbd_cdc_acm_t *cdc);

/** The line coding last set by the host (115200 8N1 until then). */
usbd_cdc_acm_line_coding_t usbd_cdc_acm_line_coding(const usbd_cdc_acm_t *cdc);

#ifdef __cplusplus
}
#endif

#endif /* USBD_CDC_ACM_H */
