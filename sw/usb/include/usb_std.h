/**
 * @file usb_std.h
 * @brief Definitions from the USB 2.0 specification: setup packet, request and
 *        descriptor codes, and macros that build descriptors as byte lists.
 */
#ifndef USB_STD_H
#define USB_STD_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ---- Setup packet -------------------------------------------------------- */

/** A decoded setup packet. */
typedef struct {
    uint8_t bmRequestType;
    uint8_t bRequest;
    uint16_t wValue;
    uint16_t wIndex;
    uint16_t wLength;
} usb_setup_t;

/** Decode the 8 bytes of a SETUP data packet (little-endian fields). */
static inline void usb_setup_parse(usb_setup_t *setup, const uint8_t raw[8])
{
    setup->bmRequestType = raw[0];
    setup->bRequest = raw[1];
    setup->wValue = (uint16_t)(raw[2] | ((uint16_t)raw[3] << 8));
    setup->wIndex = (uint16_t)(raw[4] | ((uint16_t)raw[5] << 8));
    setup->wLength = (uint16_t)(raw[6] | ((uint16_t)raw[7] << 8));
}

#define USB_REQ_DIR_IN 0x80u
#define USB_REQ_TYPE_MASK 0x60u
#define USB_REQ_TYPE_STANDARD 0x00u
#define USB_REQ_TYPE_CLASS 0x20u
#define USB_REQ_TYPE_VENDOR 0x40u
#define USB_REQ_RECIPIENT_MASK 0x1Fu
#define USB_REQ_RECIPIENT_DEVICE 0x00u
#define USB_REQ_RECIPIENT_INTERFACE 0x01u
#define USB_REQ_RECIPIENT_ENDPOINT 0x02u

#define USB_REQ_GET_STATUS 0x00u
#define USB_REQ_CLEAR_FEATURE 0x01u
#define USB_REQ_SET_FEATURE 0x03u
#define USB_REQ_SET_ADDRESS 0x05u
#define USB_REQ_GET_DESCRIPTOR 0x06u
#define USB_REQ_SET_DESCRIPTOR 0x07u
#define USB_REQ_GET_CONFIGURATION 0x08u
#define USB_REQ_SET_CONFIGURATION 0x09u
#define USB_REQ_GET_INTERFACE 0x0Au
#define USB_REQ_SET_INTERFACE 0x0Bu
#define USB_REQ_SYNCH_FRAME 0x0Cu

#define USB_FEATURE_ENDPOINT_HALT 0x00u
#define USB_FEATURE_DEVICE_REMOTE_WAKEUP 0x01u
#define USB_FEATURE_TEST_MODE 0x02u

/* ---- Descriptors ---------------------------------------------------------- */

#define USB_DESC_TYPE_DEVICE 0x01u
#define USB_DESC_TYPE_CONFIGURATION 0x02u
#define USB_DESC_TYPE_STRING 0x03u
#define USB_DESC_TYPE_INTERFACE 0x04u
#define USB_DESC_TYPE_ENDPOINT 0x05u
#define USB_DESC_TYPE_DEVICE_QUALIFIER 0x06u
#define USB_DESC_TYPE_OTHER_SPEED_CONFIGURATION 0x07u
#define USB_DESC_TYPE_INTERFACE_ASSOCIATION 0x0Bu
#define USB_DESC_TYPE_BOS 0x0Fu
#define USB_DESC_TYPE_CS_INTERFACE 0x24u
#define USB_DESC_TYPE_CS_ENDPOINT 0x25u

#define USB_DESC_DEVICE_LEN 18u
#define USB_DESC_CONFIGURATION_LEN 9u
#define USB_DESC_INTERFACE_LEN 9u
#define USB_DESC_ENDPOINT_LEN 7u
#define USB_DESC_IAD_LEN 8u

/** bmAttributes of a configuration descriptor. */
#define USB_CONFIG_ATTR_RESERVED 0x80u
#define USB_CONFIG_ATTR_SELF_POWERED 0x40u
#define USB_CONFIG_ATTR_REMOTE_WAKEUP 0x20u

/** Endpoint transfer types (bmAttributes bits 1:0 of an endpoint descriptor). */
typedef enum {
    USB_EP_CONTROL = 0,
    USB_EP_ISOCHRONOUS = 1,
    USB_EP_BULK = 2,
    USB_EP_INTERRUPT = 3
} usb_ep_type_t;

#define USB_EP_DIR_IN 0x80u
#define USB_EP_NUMBER(ep_addr) ((uint8_t)((ep_addr) & 0x0Fu))
#define USB_EP_IS_IN(ep_addr) (((ep_addr) & USB_EP_DIR_IN) != 0u)

/** A 16-bit value as two little-endian bytes for descriptor lists. */
#define USB_U16(x) (uint8_t)((x) & 0xFFu), (uint8_t)(((x) >> 8) & 0xFFu)

/** Device descriptor (18 bytes). */
#define USB_DESC_DEVICE(bcd_usb, cls, subclass, protocol, ep0_size, vid, pid, bcd_device, \
                        i_manufacturer, i_product, i_serial, num_configurations)          \
    USB_DESC_DEVICE_LEN, USB_DESC_TYPE_DEVICE, USB_U16(bcd_usb), (cls), (subclass),       \
        (protocol), (ep0_size), USB_U16(vid), USB_U16(pid), USB_U16(bcd_device),            \
        (i_manufacturer), (i_product), (i_serial), (num_configurations)

/** Configuration descriptor header (9 bytes); @p total_len covers everything below it. */
#define USB_DESC_CONFIGURATION(total_len, num_interfaces, value, i_configuration, attributes, \
                               max_power_ma)                                                  \
    USB_DESC_CONFIGURATION_LEN, USB_DESC_TYPE_CONFIGURATION, USB_U16(total_len),                \
        (num_interfaces), (value), (i_configuration), (USB_CONFIG_ATTR_RESERVED | (attributes)), \
        (uint8_t)((max_power_ma) / 2u)

/** Interface association descriptor (8 bytes). */
#define USB_DESC_IAD(first_interface, interface_count, cls, subclass, protocol, i_function) \
    USB_DESC_IAD_LEN, USB_DESC_TYPE_INTERFACE_ASSOCIATION, (first_interface),             \
        (interface_count), (cls), (subclass), (protocol), (i_function)

/** Interface descriptor (9 bytes). */
#define USB_DESC_INTERFACE(number, alternate, num_endpoints, cls, subclass, protocol, i_interface) \
    USB_DESC_INTERFACE_LEN, USB_DESC_TYPE_INTERFACE, (number), (alternate), (num_endpoints),      \
        (cls), (subclass), (protocol), (i_interface)

/** Endpoint descriptor (7 bytes). */
#define USB_DESC_ENDPOINT(ep_addr, type, max_packet, interval)                         \
    USB_DESC_ENDPOINT_LEN, USB_DESC_TYPE_ENDPOINT, (ep_addr), (type), USB_U16(max_packet), \
        (interval)

#ifdef __cplusplus
}
#endif

#endif /* USB_STD_H */
