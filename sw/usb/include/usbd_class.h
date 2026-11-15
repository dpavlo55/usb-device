/**
 * @file usbd_class.h
 * @brief Interface between the device stack and class drivers (CDC, HID, MSC, ...).
 *
 * A class driver claims interfaces of the active configuration, opens their
 * endpoints, answers the requests addressed to them and handles their transfers.
 * Several class drivers can be registered with one device (a composite device).
 */
#ifndef USBD_CLASS_H
#define USBD_CLASS_H

#include "usb_common.h"
#include "usb_std.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct usbd usbd_t;

/** When a control request handler is called. */
typedef enum {
    USBD_CTRL_STAGE_SETUP,  /**< The request arrived; answer it or ask for its data. */
    USBD_CTRL_STAGE_DATA    /**< The OUT data asked for with usbd_ctrl_receive() arrived. */
} usbd_ctrl_stage_t;

/**
 * Handler for a control request. In the SETUP stage it either answers with
 * usbd_ctrl_send(), asks for the OUT data stage with usbd_ctrl_receive(), or does
 * neither for a request without data. Returning false refuses the request (STALL).
 */
typedef bool (*usbd_control_handler_t)(usbd_t *dev, void *ctx, usbd_ctrl_stage_t stage,
                                       const usb_setup_t *setup);

typedef struct usbd_class {
    const char *name;

    /** Bus reset or deconfiguration: forget the configuration (endpoints are closed already). */
    void (*reset)(usbd_t *dev, void *ctx);

    /**
     * Offered the descriptors of the active configuration from an interface (or the
     * interface association descriptor before it) to the end. Claim the interfaces
     * this driver serves, open their endpoints with usbd_ep_open(), and return the
     * number of descriptor bytes consumed; return 0 to leave them to another driver.
     * Also called on SET_INTERFACE with the descriptors of the alternate setting.
     */
    uint16_t (*open)(usbd_t *dev, void *ctx, const uint8_t *desc, uint16_t length);

    /** Class or vendor request to one of its interfaces or endpoints (may be NULL). */
    usbd_control_handler_t control;

    /** A transfer on one of its endpoints finished (@p length bytes moved). */
    void (*xfer_done)(usbd_t *dev, void *ctx, uint8_t ep_addr, uint32_t length);
} usbd_class_t;

#ifdef __cplusplus
}
#endif

#endif /* USBD_CLASS_H */
