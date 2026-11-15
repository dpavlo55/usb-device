/**
 * @file usb_hal.h
 * @brief Layer 2: endpoints, buffers and transfers of the USB device core.
 *
 * The HAL owns the core: it maps USB endpoints to the core's 16 entries, allocates
 * their buffers in the shared RAM, splits IN transfers into packets and gathers OUT
 * packets into transfers. It reports bus resets, suspend and resume, SETUP packets
 * and finished transfers through one event callback. It knows nothing about
 * requests or classes.
 *
 * All functions take the HAL instance, so one program can drive several cores.
 * usb_hal_process() and the callback it calls must not run concurrently with the
 * other functions on the same instance; usbd.h describes how the device stack
 * arranges that.
 */
#ifndef USB_HAL_H
#define USB_HAL_H

#include "usb_common.h"
#include "usb_ll.h"
#include "usb_std.h"

#ifdef __cplusplus
extern "C" {
#endif

/** What the HAL reports. */
typedef enum {
    USB_HAL_EVENT_BUS_RESET,  /**< Bus reset: address 0, every endpoint except EP0 closed. */
    USB_HAL_EVENT_SETUP,      /**< A SETUP packet on EP0; any EP0 transfer was aborted. */
    USB_HAL_EVENT_XFER_DONE,  /**< A transfer finished. */
    USB_HAL_EVENT_SUSPEND,    /**< The bus has been idle for 3 ms: enter suspend. */
    USB_HAL_EVENT_RESUME,     /**< Bus activity ended a suspend (resume signaling or a bus reset). */
    USB_HAL_EVENT_VBUS        /**< VBUS appeared or went away; usb_hal_vbus_present() has the level. */
} usb_hal_event_type_t;

typedef struct {
    usb_hal_event_type_t type;
    uint8_t ep_addr;    /**< XFER_DONE: endpoint address (bit 7 set for IN). */
    uint32_t length;    /**< XFER_DONE: bytes moved. */
    uint8_t setup[8];   /**< SETUP: the packet. */
} usb_hal_event_t;

typedef struct usb_hal usb_hal_t;
typedef void (*usb_hal_event_cb_t)(usb_hal_t *hal, const usb_hal_event_t *event, void *ctx);

/** @cond INTERNAL */
typedef struct {
    uint8_t ep_addr;
    uint8_t type;
    uint8_t open;
    uint8_t busy;
    uint8_t zlp;
    uint16_t max_packet;
    uint16_t buffer_size;
    uint16_t ram_offset;
    uint16_t flight[2];     /* IN bytes armed in each buffer */
    uint8_t buffers;        /* 1, or 2 with entry k+1 as the second buffer (0 on that entry) */
    uint16_t transfer_size; /* bytes one arm may move: max_packet, or the buffer for split IN */
    uint8_t arm_turn;       /* buffer armed next */
    uint8_t done_turn;      /* buffer that completes next */
    uint8_t armed;          /* buffers armed */
    uint32_t control;       /* shadow of the entry's control register */
    uint8_t *data;
    uint32_t length;
    uint32_t done;
} usb_hal_entry_t;

struct usb_hal {
    uintptr_t base;
    volatile uint32_t *ram;
    uint32_t ram_size;
    uint32_t ram_next;
    uint32_t ram_reserved;   /* RAM kept by EP0 across bus resets and closes */
    uint32_t control;        /* shadow of the global control register */
    usb_hal_event_cb_t on_event;
    void *ctx;
    uint8_t entry_of[2][16]; /* [direction in][number] -> entry, 0xFF if none */
    usb_hal_entry_t entry[USB_LL_ENTRIES];
};
/** @endcond */

/** Entries of EP0: OUT (it also receives SETUP packets) and IN. */
#define USB_HAL_ENTRY_EP0_OUT 0u
#define USB_HAL_ENTRY_EP0_IN 1u

/**
 * Take over the core at @p base: enable it with the pull-up off, enable its
 * interrupts and open EP0 with USB_EP0_SIZE.
 */
usb_status_t usb_hal_init(usb_hal_t *hal, uintptr_t base, usb_hal_event_cb_t on_event, void *ctx);

/** Request the D+ pull-up; hardware drives it only while VBUS is present. */
void usb_hal_connect(usb_hal_t *hal, bool connect);

/** True while VBUS is present (status.vbus_present); every change raises USB_HAL_EVENT_VBUS. */
bool usb_hal_vbus_present(const usb_hal_t *hal);

/** True while the bus is suspended (status.suspended). */
bool usb_hal_suspended(const usb_hal_t *hal);

/** Frame number of the last good SOF (0 to 2047; 0 after reset, held during suspend). */
uint16_t usb_hal_frame_number(const usb_hal_t *hal);

/**
 * Signal remote wakeup. Hardware ignores it unless the bus is suspended, waits until
 * the bus has been idle for 5 ms (TWTRSM), drives K for 2 ms and releases the bus; the
 * host's resume signaling then ends the suspend (USB_HAL_EVENT_RESUME). Allowed only
 * while the host has enabled DEVICE_REMOTE_WAKEUP; the HAL does not check that.
 */
void usb_hal_remote_wakeup(usb_hal_t *hal);

/** Answer tokens for @p address from now on. */
void usb_hal_set_address(usb_hal_t *hal, uint8_t address);

/**
 * Open an endpoint. Reopening an endpoint keeps its entry and buffer when the
 * buffer is large enough. Buffers are released only by usb_hal_ep_close_all().
 */
usb_status_t usb_hal_ep_open(usb_hal_t *hal, uint8_t ep_addr, usb_ep_type_t type, uint16_t max_packet);

/**
 * Open a double-buffered endpoint (not CONTROL): two consecutive entries and two
 * buffers that hardware uses in turn, so the host can move the next packet while
 * the previous one is handled. usb_hal_ep_arm_raw() then accepts two packets;
 * usb_hal_ep_xfer() works too, one packet at a time. Reopening keeps the entries
 * only when the endpoint was double-buffered before.
 */
usb_status_t usb_hal_ep_open_double(usb_hal_t *hal, uint8_t ep_addr, usb_ep_type_t type, uint16_t max_packet);

/**
 * Open an endpoint with @p buffers buffers (1 or 2, see usb_hal_ep_open_double()) of
 * @p transfer_size bytes each, rounded up to a power of two. For an IN endpoint with
 * a power-of-two @p max_packet, a larger @p transfer_size lets one arm move that many
 * bytes, which hardware sends as max_packet packets with one completion (one
 * USB_HAL_EVENT_XFER_DONE per arm); it cuts the per-packet cost of usb_hal_ep_xfer()
 * and usb_hal_ep_arm_raw(). OUT endpoints take one packet per arm.
 */
usb_status_t usb_hal_ep_open_ex(usb_hal_t *hal, uint8_t ep_addr, usb_ep_type_t type, uint16_t max_packet,
                                uint16_t transfer_size, uint8_t buffers);

/** Disable an endpoint; its buffer stays reserved for a later reopen. */
usb_status_t usb_hal_ep_close(usb_hal_t *hal, uint8_t ep_addr);

/** Close every endpoint except EP0 and release their buffers. */
void usb_hal_ep_close_all(usb_hal_t *hal);

/**
 * Start a transfer. IN: send @p length bytes from @p data in packets, then a
 * zero-length packet if @p zlp is set and the last packet was full. OUT: receive
 * into @p data until @p length bytes or a short packet arrive. @p data must stay
 * valid until USB_HAL_EVENT_XFER_DONE.
 */
usb_status_t usb_hal_ep_xfer(usb_hal_t *hal, uint8_t ep_addr, void *data, uint32_t length, bool zlp);

/**
 * Forget the transfer in progress. On EP0 this is safe: the next SETUP disarms
 * EP0 in hardware. On other endpoints a packet already armed stays armed.
 */
usb_status_t usb_hal_ep_abort(usb_hal_t *hal, uint8_t ep_addr);

/** True while a transfer is in progress. */
bool usb_hal_ep_busy(const usb_hal_t *hal, uint8_t ep_addr);

/** Halt or resume an endpoint; resuming also returns its data toggle to DATA0. */
usb_status_t usb_hal_ep_stall(usb_hal_t *hal, uint8_t ep_addr, bool stall);

/** True while the endpoint is halted. */
bool usb_hal_ep_stalled(const usb_hal_t *hal, uint8_t ep_addr);

/** Halt EP0 in both directions until the next SETUP (a request error). */
void usb_hal_ep0_stall(usb_hal_t *hal);

/**
 * Zero-copy access: the buffer in the shared RAM (word accesses only) that the next
 * usb_hal_ep_arm_raw() arms, and its size. Fill it and arm it; do not mix this with
 * usb_hal_ep_xfer() on the same endpoint.
 */
volatile uint32_t *usb_hal_ep_buffer(const usb_hal_t *hal, uint8_t ep_addr, uint32_t *size);

/** The buffer of the endpoint's last finished packet: read OUT data from it in the
    USB_HAL_EVENT_XFER_DONE callback of a raw transfer. */
volatile uint32_t *usb_hal_ep_done_buffer(const usb_hal_t *hal, uint8_t ep_addr);

/**
 * Arm @p length bytes in usb_hal_ep_buffer() (zero-copy path): one packet, or up to
 * the transfer size of usb_hal_ep_open_ex() for a split IN endpoint (no zero-length
 * packet follows a full last packet; arm one with length 0). Each arm finishes with
 * its own USB_HAL_EVENT_XFER_DONE, in the order armed. USB_ERR_BUSY when every
 * buffer of the endpoint is armed.
 */
usb_status_t usb_hal_ep_arm_raw(usb_hal_t *hal, uint8_t ep_addr, uint32_t length);

/** Handle every pending event of the core, calling the event callback for each. */
void usb_hal_process(usb_hal_t *hal);

#ifdef __cplusplus
}
#endif

#endif /* USB_HAL_H */
