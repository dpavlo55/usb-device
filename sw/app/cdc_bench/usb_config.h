/**
 * @file usb_config.h
 * @brief USB device library configuration of the CDC-ACM benchmark
 *        (see sw/usb/include/usb_config_default.h for every option).
 */
#ifndef USB_CONFIG_H
#define USB_CONFIG_H

#include <stdint.h>

#define USB_EP0_SIZE 64u
#define USBD_MAX_CLASSES 1u
#define USBD_MAX_INTERFACES 2u
#define USBD_CTRL_BUFFER_SIZE 128u
#define USBD_CDC_ACM_RX_SIZE 2048u
#define USBD_CDC_ACM_TX_SIZE 2048u
#define USBD_CDC_ACM_IN_TRANSFER 512u

/* Timestamps (mcycle) of the profile points on the packet path, read by main.c;
   only while tracing ("trace 1"), so throughput runs are not slowed down. */
extern volatile uint32_t usb_profile[8];
extern volatile uint8_t usb_tracing;
#define USB_PROFILE(point)                                    \
    do {                                                      \
        if (usb_tracing) {                                    \
            uint32_t usb_profile_now;                         \
            __asm__ volatile("csrr %0, mcycle" : "=r"(usb_profile_now)); \
            usb_profile[(point)] = usb_profile_now;           \
        }                                                     \
    } while (0)

#endif /* USB_CONFIG_H */
