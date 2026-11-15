/**
 * @file usbd_std.c
 * @brief Layer 3: standard requests (USB 2.0 section 9.4) to the device, its
 *        interfaces and its endpoints.
 */
#include "usbd_internal.h"

static bool send_status(usbd_t *dev, uint16_t status)
{
    uint8_t *buffer = usbd_ctrl_buffer(dev);
    buffer[0] = (uint8_t)status;
    buffer[1] = (uint8_t)(status >> 8);
    usbd_ctrl_send(dev, buffer, 2u);
    return true;
}

/* Attributes (bmAttributes) of the active configuration, or of the first one. */
static uint8_t config_attributes(const usbd_t *dev)
{
    const uint8_t *config = usbd_active_configuration(dev);
    if (config == NULL && dev->descriptors->num_configurations > 0u) {
        config = dev->descriptors->configurations[0];
    }
    return config != NULL ? config[7] : 0u;
}

static bool send_string(usbd_t *dev, uint8_t index)
{
    const usbd_descriptors_t *d = dev->descriptors;
    uint8_t *buffer = usbd_ctrl_buffer(dev);
    uint32_t length;

    if (index == 0u) {
        buffer[2] = (uint8_t)d->language_id;
        buffer[3] = (uint8_t)(d->language_id >> 8);
        length = 4u;
    } else if (index <= d->num_strings && d->strings[index - 1u] != NULL) {
        const char *text = d->strings[index - 1u];
        length = 2u;
        while (*text != '\0' && length + 2u <= USBD_CTRL_BUFFER_SIZE && length + 2u <= 255u) {
            buffer[length++] = (uint8_t)*text++;
            buffer[length++] = 0u;
        }
    } else {
        return false;
    }
    buffer[0] = (uint8_t)length;
    buffer[1] = USB_DESC_TYPE_STRING;
    usbd_ctrl_send(dev, buffer, (uint16_t)length);
    return true;
}

static bool get_descriptor(usbd_t *dev, const usb_setup_t *setup)
{
    const usbd_descriptors_t *d = dev->descriptors;
    const uint8_t type = (uint8_t)(setup->wValue >> 8);
    const uint8_t index = (uint8_t)setup->wValue;

    switch (type) {
    case USB_DESC_TYPE_DEVICE:
        usbd_ctrl_send(dev, d->device, USB_DESC_DEVICE_LEN);
        return true;
    case USB_DESC_TYPE_CONFIGURATION:
        if (index >= d->num_configurations) {
            return false;
        }
        usbd_ctrl_send(dev, d->configurations[index],
                       (uint16_t)(d->configurations[index][2] | ((uint16_t)d->configurations[index][3] << 8)));
        return true;
    case USB_DESC_TYPE_STRING:
        return send_string(dev, index);
    default:
        /* Full-speed only: no DEVICE_QUALIFIER or OTHER_SPEED_CONFIGURATION. */
        return false;
    }
}

static bool device_request(usbd_t *dev, const usb_setup_t *setup)
{
    switch (setup->bRequest) {
    case USB_REQ_GET_STATUS: {
        uint16_t status = 0u;
        if ((config_attributes(dev) & USB_CONFIG_ATTR_SELF_POWERED) != 0u) {
            status |= 1u;
        }
        if (dev->remote_wakeup) {
            status |= 2u;
        }
        return send_status(dev, status);
    }
    case USB_REQ_CLEAR_FEATURE:
    case USB_REQ_SET_FEATURE:
        if (setup->wValue != USB_FEATURE_DEVICE_REMOTE_WAKEUP ||
            (config_attributes(dev) & USB_CONFIG_ATTR_REMOTE_WAKEUP) == 0u) {
            return false;
        }
        dev->remote_wakeup = setup->bRequest == USB_REQ_SET_FEATURE ? 1u : 0u;
        return true;
    case USB_REQ_SET_ADDRESS:
        if (setup->wValue > 127u || setup->wIndex != 0u || setup->wLength != 0u ||
            dev->state == USBD_STATE_CONFIGURED) {
            return false;
        }
        dev->ctrl.set_address = 1u;
        dev->ctrl.address = (uint8_t)setup->wValue;
        return true;
    case USB_REQ_GET_DESCRIPTOR:
        return get_descriptor(dev, setup);
    case USB_REQ_GET_CONFIGURATION: {
        uint8_t *buffer = usbd_ctrl_buffer(dev);
        buffer[0] = dev->configuration;
        usbd_ctrl_send(dev, buffer, 1u);
        return true;
    }
    case USB_REQ_SET_CONFIGURATION: {
        if (dev->state == USBD_STATE_DEFAULT || dev->state == USBD_STATE_DETACHED || setup->wValue > 255u) {
            return false;
        }
        const uint8_t value = (uint8_t)setup->wValue;
        if (value == 0u) {
            usbd_deconfigure(dev);
            usbd_set_state(dev, USBD_STATE_ADDRESS);
            return true;
        }
        if (usbd_configure(dev, value) != USB_OK) {
            return false;
        }
        usbd_set_state(dev, USBD_STATE_CONFIGURED);
        return true;
    }
    default:
        return false;
    }
}

static bool interface_request(usbd_t *dev, const usb_setup_t *setup)
{
    const uint8_t interface = (uint8_t)setup->wIndex;
    if (dev->state != USBD_STATE_CONFIGURED || interface >= USBD_MAX_INTERFACES ||
        dev->interface_owner[interface] == USBD_NO_OWNER) {
        return false;
    }
    switch (setup->bRequest) {
    case USB_REQ_GET_STATUS:
        return send_status(dev, 0u);
    case USB_REQ_GET_INTERFACE: {
        uint8_t *buffer = usbd_ctrl_buffer(dev);
        buffer[0] = dev->interface_alt[interface];
        usbd_ctrl_send(dev, buffer, 1u);
        return true;
    }
    case USB_REQ_SET_INTERFACE:
        return usbd_set_interface(dev, interface, (uint8_t)setup->wValue) == USB_OK;
    default:
        return false;
    }
}

static bool endpoint_request(usbd_t *dev, const usb_setup_t *setup)
{
    const uint8_t ep_addr = (uint8_t)(setup->wIndex & 0x8Fu);
    const bool ep0 = USB_EP_NUMBER(ep_addr) == 0u;
    if (!ep0 && (dev->state != USBD_STATE_CONFIGURED ||
                 dev->endpoint_owner[USB_EP_IS_IN(ep_addr) ? 1u : 0u][USB_EP_NUMBER(ep_addr)] == USBD_NO_OWNER)) {
        return false;
    }
    switch (setup->bRequest) {
    case USB_REQ_GET_STATUS:
        return send_status(dev, (!ep0 && usb_hal_ep_stalled(&dev->hal, ep_addr)) ? 1u : 0u);
    case USB_REQ_CLEAR_FEATURE:
    case USB_REQ_SET_FEATURE:
        if (setup->wValue != USB_FEATURE_ENDPOINT_HALT) {
            return false;
        }
        if (!ep0) {
            (void)usb_hal_ep_stall(&dev->hal, ep_addr, setup->bRequest == USB_REQ_SET_FEATURE);
        }
        return true;
    default:
        return false;
    }
}

bool usbd_std_request(usbd_t *dev, void *ctx, usbd_ctrl_stage_t stage, const usb_setup_t *setup)
{
    (void)ctx;
    if (stage != USBD_CTRL_STAGE_SETUP) {
        return false;
    }
    switch (setup->bmRequestType & USB_REQ_RECIPIENT_MASK) {
    case USB_REQ_RECIPIENT_DEVICE:
        return device_request(dev, setup);
    case USB_REQ_RECIPIENT_INTERFACE:
        return interface_request(dev, setup);
    case USB_REQ_RECIPIENT_ENDPOINT:
        return endpoint_request(dev, setup);
    default:
        return false;
    }
}
