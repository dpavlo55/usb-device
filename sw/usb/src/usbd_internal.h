/**
 * @file usbd_internal.h
 * @brief Functions shared by the device stack's source files; not part of the API.
 */
#ifndef USBD_INTERNAL_H
#define USBD_INTERNAL_H

#include "usbd.h"

/* usbd_ctrl.c */
void usbd_ctrl_reset(usbd_t *dev);
void usbd_ctrl_setup(usbd_t *dev, const uint8_t raw[8]);
void usbd_ctrl_xfer_done(usbd_t *dev, uint8_t ep_addr, uint32_t length);

/* usbd_std.c: handler for standard requests to the device, interfaces and endpoints */
bool usbd_std_request(usbd_t *dev, void *ctx, usbd_ctrl_stage_t stage, const usb_setup_t *setup);

/* usbd_core.c */
void usbd_set_state(usbd_t *dev, usbd_state_t state);
const uint8_t *usbd_active_configuration(const usbd_t *dev);
void usbd_deconfigure(usbd_t *dev);
usb_status_t usbd_configure(usbd_t *dev, uint8_t value);
usb_status_t usbd_set_interface(usbd_t *dev, uint8_t interface, uint8_t alternate);
usbd_control_handler_t usbd_route(usbd_t *dev, const usb_setup_t *setup, void **ctx);

#endif /* USBD_INTERNAL_H */
