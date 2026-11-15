/**
 * @file usb_config.h
 * @brief USB device library configuration of the CDC-ACM echo example
 *        (see sw/usb/include/usb_config_default.h for every option).
 */
#ifndef USB_CONFIG_H
#define USB_CONFIG_H

#define USB_EP0_SIZE 64u
#define USBD_MAX_CLASSES 1u
#define USBD_MAX_INTERFACES 2u
#define USBD_CTRL_BUFFER_SIZE 128u

#endif /* USB_CONFIG_H */
