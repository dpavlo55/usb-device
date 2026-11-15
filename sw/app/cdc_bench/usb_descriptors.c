/**
 * @file usb_descriptors.c
 * @brief Descriptors of the CDC-ACM benchmark: one configuration with one
 *        CDC-ACM function (interfaces 0 and 1).
 *
 * VID 0x1209 / PID 0x0001 is the pid.codes test identity: fine for development,
 * not for a product.
 */
#include "usb_descriptors.h"

#include "usbd_cdc_acm.h"

#define CONFIG_TOTAL_LEN (USB_DESC_CONFIGURATION_LEN + USBD_CDC_ACM_DESC_LEN)

static const uint8_t device_descriptor[] = {
    /* Class 0xEF/0x02/0x01: the functions are described by interface associations. */
    USB_DESC_DEVICE(0x0200u, 0xEFu, 0x02u, 0x01u, USB_EP0_SIZE, 0x1209u, 0x0001u, 0x0100u, 1u, 2u, 3u, 1u),
};

static const uint8_t configuration_descriptor[] = {
    USB_DESC_CONFIGURATION(CONFIG_TOTAL_LEN, 2u, 1u, 0u, 0u, 100u),
    USBD_CDC_ACM_DESCRIPTOR(0u, 4u, CDC_EP_NOTIFY, CDC_EP_OUT, CDC_EP_IN, 64u),
};

_Static_assert(sizeof device_descriptor == USB_DESC_DEVICE_LEN, "device descriptor length");
_Static_assert(sizeof configuration_descriptor == CONFIG_TOTAL_LEN, "configuration descriptor length");

static const uint8_t *const configurations[] = { configuration_descriptor };

static const char *const strings[] = {
    "usb-device",        /* 1: manufacturer */
    "CDC-ACM bench",     /* 2: product */
    "0001",              /* 3: serial number */
    "Virtual COM port",  /* 4: CDC function */
};

const usbd_descriptors_t app_descriptors = {
    .device = device_descriptor,
    .configurations = configurations,
    .num_configurations = 1u,
    .strings = strings,
    .num_strings = (uint8_t)(sizeof strings / sizeof strings[0]),
    .language_id = 0x0409u,
};
