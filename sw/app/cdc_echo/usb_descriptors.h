/**
 * @file usb_descriptors.h
 * @brief Descriptors and endpoint addresses of the CDC-ACM echo example.
 */
#ifndef USB_DESCRIPTORS_H
#define USB_DESCRIPTORS_H

#include "usbd.h"

#define CDC_EP_NOTIFY 0x81u
#define CDC_EP_OUT 0x02u
#define CDC_EP_IN 0x82u

extern const usbd_descriptors_t app_descriptors;

#endif /* USB_DESCRIPTORS_H */
