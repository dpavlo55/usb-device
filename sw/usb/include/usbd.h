/**
 * @file usbd.h
 * @brief Layer 3: USB device stack (device states, control transfers, standard
 *        requests) and its interface to applications and class drivers.
 *
 * Event handling: usbd_process() handles every pending event and returns. Call it
 * from the USB interrupt (lowest load and latency; class and application callbacks
 * then run in the interrupt and must be short), or let the interrupt only mask
 * itself and signal a task that calls usbd_process() and unmasks it again, or call
 * it cyclically without interrupts. The core NAKs the host while software is not
 * ready, so late processing never breaks the bus, it only lowers throughput.
 *
 * Functions called from thread context while usbd_process() may run in the
 * interrupt must be protected with usb_port_enter_critical(); class drivers do
 * that in their thread-context API (see usbd_cdc_acm.c).
 */
#ifndef USBD_H
#define USBD_H

#include "usb_common.h"
#include "usb_hal.h"
#include "usb_std.h"
#include "usbd_class.h"

#ifdef __cplusplus
extern "C" {
#endif

/** Device states (USB 2.0 section 9.1.1). */
typedef enum {
    USBD_STATE_DETACHED,   /**< Pull-up off. */
    USBD_STATE_DEFAULT,    /**< Attached or reset, address 0. */
    USBD_STATE_ADDRESS,    /**< Address assigned, not configured. */
    USBD_STATE_CONFIGURED  /**< A configuration is active. */
} usbd_state_t;

/** Descriptors of the device, all in read-only memory. */
typedef struct {
    const uint8_t *device;                  /**< Device descriptor (18 bytes). */
    const uint8_t *const *configurations;   /**< Configuration descriptors with everything below them. */
    uint8_t num_configurations;
    const char *const *strings;             /**< ASCII strings for string descriptor indices 1..num_strings. */
    uint8_t num_strings;
    uint16_t language_id;                   /**< String descriptor 0, e.g. 0x0409 (English, US). */
} usbd_descriptors_t;

/** Application callbacks; any may be NULL. */
typedef struct {
    void (*state_changed)(usbd_t *dev, usbd_state_t state, void *ctx);
    /** The bus was suspended (true) or resumed (false); runs in usbd_process(). */
    void (*suspend_changed)(usbd_t *dev, bool suspended, void *ctx);
    /** Vendor requests to the device (recipient device); same rules as class handlers. */
    usbd_control_handler_t vendor_request;
    void *ctx;
} usbd_app_t;

/** @cond INTERNAL */
#define USBD_NO_OWNER 0xFFu

typedef enum { CTRL_IDLE, CTRL_DATA_IN, CTRL_DATA_OUT, CTRL_STATUS_IN, CTRL_STATUS_OUT } usbd_ctrl_state_t;

typedef struct {
    usbd_ctrl_state_t state;
    usb_setup_t setup;
    usbd_control_handler_t handler;
    void *handler_ctx;
    const uint8_t *tx;
    uint16_t tx_length;
    uint8_t *rx;
    uint16_t rx_length;
    uint8_t answered;
    uint8_t status_done;
    uint8_t set_address;
    uint8_t address;
    uint8_t buffer[USBD_CTRL_BUFFER_SIZE] USB_ALIGNED(4);
} usbd_ctrl_t;

struct usbd {
    usb_hal_t hal;
    const usbd_descriptors_t *descriptors;
    usbd_app_t app;
    usbd_state_t state;
    uint8_t configuration;      /* bConfigurationValue, 0 if not configured */
    uint8_t config_index;
    uint8_t remote_wakeup;
    uint8_t connect_requested;  /* usbd_connect(dev, true); attached only while VBUS is present too */
    uint8_t suspended;          /* bus suspended (USB 2.0 section 9.1.1.6), orthogonal to state */
    uint8_t num_classes;
    uint8_t opening;            /* class index during open(), for endpoint ownership */
    const usbd_class_t *classes[USBD_MAX_CLASSES];
    void *class_ctx[USBD_MAX_CLASSES];
    uint8_t interface_owner[USBD_MAX_INTERFACES];
    uint8_t interface_alt[USBD_MAX_INTERFACES];
    uint8_t endpoint_owner[2][16];
    usbd_ctrl_t ctrl;
};
/** @endcond */

/** Take over the core at @p base with the pull-up off. */
usb_status_t usbd_init(usbd_t *dev, uintptr_t base, const usbd_descriptors_t *descriptors, const usbd_app_t *app);

/** Register a class driver instance; call before usbd_connect(). */
usb_status_t usbd_register_class(usbd_t *dev, const usbd_class_t *driver, void *ctx);

/**
 * Ask to attach to (true) or detach from the bus. The device attaches (state
 * DEFAULT) only while VBUS is present as well (USB 2.0 section 7.1.5.1), and
 * follows VBUS from then on: usbd_process() handles every VBUS change, so on
 * unplug the configuration closes and the state returns to DETACHED with address
 * 0, and on replug the device attaches again. Call from thread context.
 */
void usbd_connect(usbd_t *dev, bool connect);

/** Handle every pending event of the core. */
void usbd_process(usbd_t *dev);

usbd_state_t usbd_state(const usbd_t *dev);

/** True if the host enabled remote wakeup (SET_FEATURE(DEVICE_REMOTE_WAKEUP)). */
bool usbd_remote_wakeup_enabled(const usbd_t *dev);

/** True while the bus is suspended; the device keeps its state, address and configuration. */
bool usbd_suspended(const usbd_t *dev);

/** Frame number of the last good SOF (0 to 2047), e.g. to time-stamp data. */
uint16_t usbd_frame_number(const usbd_t *dev);

/**
 * Wake the host from suspend. Only while suspended and while the host has enabled
 * remote wakeup (else USB_ERR_STATE). Hardware waits until the bus has been idle for
 * 5 ms, drives resume (K) for 2 ms, and the host's own resume then ends the suspend
 * (suspend_changed(false)). Call from thread context.
 */
usb_status_t usbd_remote_wakeup(usbd_t *dev);

/* ---- For class drivers ---------------------------------------------------- */

/** Open the endpoint described by an endpoint descriptor; called from a class open(). */
usb_status_t usbd_ep_open(usbd_t *dev, const uint8_t *endpoint_desc);

/** Like usbd_ep_open(), with two buffers that hardware uses in turn (see
    usb_hal_ep_open_double()). */
usb_status_t usbd_ep_open_double(usbd_t *dev, const uint8_t *endpoint_desc);

/** Like usbd_ep_open() with @p buffers buffers (1 or 2) of @p transfer_size bytes
    (0: one packet); see usb_hal_ep_open_ex(). */
usb_status_t usbd_ep_open_ex(usbd_t *dev, const uint8_t *endpoint_desc, uint16_t transfer_size, uint8_t buffers);

/** Start a transfer on an endpoint (see usb_hal_ep_xfer()). */
usb_status_t usbd_ep_xfer(usbd_t *dev, uint8_t ep_addr, void *data, uint32_t length, bool zlp);

/** Zero-copy access: the endpoint's buffer in the shared RAM, word accesses only
    (see usb_hal_ep_buffer()). */
volatile uint32_t *usbd_ep_buffer(const usbd_t *dev, uint8_t ep_addr);

/** The buffer of the last finished packet, for OUT data in xfer_done
    (see usb_hal_ep_done_buffer()). */
volatile uint32_t *usbd_ep_done_buffer(const usbd_t *dev, uint8_t ep_addr);

/** Arm one packet of @p length bytes in usbd_ep_buffer(); each completes with its
    own xfer_done like usbd_ep_xfer() (see usb_hal_ep_arm_raw()). */
usb_status_t usbd_ep_arm_raw(usbd_t *dev, uint8_t ep_addr, uint32_t length);

bool usbd_ep_busy(const usbd_t *dev, uint8_t ep_addr);

/** Halt or resume an endpoint. */
usb_status_t usbd_ep_stall(usbd_t *dev, uint8_t ep_addr, bool stall);

/** Answer the current control request with up to wLength bytes of @p data. */
void usbd_ctrl_send(usbd_t *dev, const void *data, uint16_t length);

/** Receive the OUT data stage of the current request into @p buffer. */
void usbd_ctrl_receive(usbd_t *dev, void *buffer, uint16_t length);

/** The stack's control buffer (USBD_CTRL_BUFFER_SIZE bytes) for building answers. */
uint8_t *usbd_ctrl_buffer(usbd_t *dev);

#ifdef __cplusplus
}
#endif

#endif /* USBD_H */
