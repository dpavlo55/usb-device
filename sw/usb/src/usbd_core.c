/**
 * @file usbd_core.c
 * @brief Layer 3: device state, HAL events, class drivers and request routing.
 */
#include "usbd_internal.h"

#include "usb_port.h"

static uint16_t desc_u16(const uint8_t *p)
{
    return (uint16_t)(p[0] | ((uint16_t)p[1] << 8));
}

static void clear_owners(usbd_t *dev)
{
    for (uint32_t i = 0u; i < USBD_MAX_INTERFACES; ++i) {
        dev->interface_owner[i] = USBD_NO_OWNER;
        dev->interface_alt[i] = 0u;
    }
    for (uint32_t d = 0u; d < 2u; ++d) {
        for (uint32_t n = 0u; n < 16u; ++n) {
            dev->endpoint_owner[d][n] = USBD_NO_OWNER;
        }
    }
}

void usbd_set_state(usbd_t *dev, usbd_state_t state)
{
    if (dev->state != state) {
        dev->state = state;
        if (dev->app.state_changed != NULL) {
            dev->app.state_changed(dev, state, dev->app.ctx);
        }
    }
}

static void set_suspended(usbd_t *dev, bool suspended)
{
    if ((dev->suspended != 0u) != suspended) {
        dev->suspended = suspended ? 1u : 0u;
        if (dev->app.suspend_changed != NULL) {
            dev->app.suspend_changed(dev, suspended, dev->app.ctx);
        }
    }
}

static void follow_vbus(usbd_t *dev);

static void on_hal_event(usb_hal_t *hal, const usb_hal_event_t *event, void *ctx)
{
    (void)hal;
    usbd_t *dev = (usbd_t *)ctx;
    switch (event->type) {
    case USB_HAL_EVENT_SUSPEND:
        if (dev->state != USBD_STATE_DETACHED) {
            set_suspended(dev, true);
        }
        break;
    case USB_HAL_EVENT_RESUME:
        set_suspended(dev, false);
        break;
    case USB_HAL_EVENT_VBUS:
        follow_vbus(dev);
        break;
    case USB_HAL_EVENT_BUS_RESET:
        set_suspended(dev, false);
        usbd_ctrl_reset(dev);
        usbd_deconfigure(dev);
        dev->remote_wakeup = 0u;
        /* Floating lines read as SE0 while detached; that is no reset from a host. */
        if (dev->state != USBD_STATE_DETACHED) {
            usbd_set_state(dev, USBD_STATE_DEFAULT);
        }
        break;
    case USB_HAL_EVENT_SETUP:
        usbd_ctrl_setup(dev, event->setup);
        break;
    case USB_HAL_EVENT_XFER_DONE: {
        if (USB_EP_NUMBER(event->ep_addr) == 0u) {
            usbd_ctrl_xfer_done(dev, event->ep_addr, event->length);
            break;
        }
        const uint8_t owner = dev->endpoint_owner[USB_EP_IS_IN(event->ep_addr) ? 1u : 0u][USB_EP_NUMBER(event->ep_addr)];
        if (owner != USBD_NO_OWNER && dev->classes[owner]->xfer_done != NULL) {
            dev->classes[owner]->xfer_done(dev, dev->class_ctx[owner], event->ep_addr, event->length);
        }
        break;
    }
    default:
        break;
    }
}

usb_status_t usbd_init(usbd_t *dev, uintptr_t base, const usbd_descriptors_t *descriptors, const usbd_app_t *app)
{
    USB_CHECK(dev != NULL && descriptors != NULL && descriptors->device != NULL, USB_ERR_PARAM);
    *dev = (usbd_t){ 0 };
    dev->descriptors = descriptors;
    if (app != NULL) {
        dev->app = *app;
    }
    dev->state = USBD_STATE_DETACHED;
    clear_owners(dev);
    return usb_hal_init(&dev->hal, base, on_hal_event, dev);
}

usb_status_t usbd_register_class(usbd_t *dev, const usbd_class_t *driver, void *ctx)
{
    USB_CHECK(dev != NULL && driver != NULL && driver->open != NULL, USB_ERR_PARAM);
    if (dev->num_classes >= USBD_MAX_CLASSES) {
        return USB_ERR_NO_RESOURCE;
    }
    dev->classes[dev->num_classes] = driver;
    dev->class_ctx[dev->num_classes] = ctx;
    dev->num_classes++;
    return USB_OK;
}

void usbd_connect(usbd_t *dev, bool connect)
{
    dev->connect_requested = connect ? 1u : 0u;
    follow_vbus(dev);
}

/* Attached exactly while an attach is requested and VBUS is present. */
static void follow_vbus(usbd_t *dev)
{
    const uint32_t irq_state = usb_port_enter_critical();
    const bool attach = dev->connect_requested != 0u && usb_hal_vbus_present(&dev->hal);
    const bool attached = dev->state != USBD_STATE_DETACHED;
    if (attach && !attached) {
        usb_hal_connect(&dev->hal, true);
        usbd_set_state(dev, USBD_STATE_DEFAULT);
    } else if (!attach && attached) {
        usb_hal_connect(&dev->hal, false);
        usbd_ctrl_reset(dev);
        usbd_deconfigure(dev);
        dev->remote_wakeup = 0u;
        usb_hal_set_address(&dev->hal, 0u);
        set_suspended(dev, false);
        usbd_set_state(dev, USBD_STATE_DETACHED);
    }
    usb_port_exit_critical(irq_state);
}

void usbd_process(usbd_t *dev)
{
    usb_hal_process(&dev->hal);
}

usbd_state_t usbd_state(const usbd_t *dev)
{
    return dev->state;
}

bool usbd_remote_wakeup_enabled(const usbd_t *dev)
{
    return dev->remote_wakeup != 0u;
}

bool usbd_suspended(const usbd_t *dev)
{
    return dev->suspended != 0u;
}

uint16_t usbd_frame_number(const usbd_t *dev)
{
    return usb_hal_frame_number(&dev->hal);
}

usb_status_t usbd_remote_wakeup(usbd_t *dev)
{
    const uint32_t irq_state = usb_port_enter_critical();
    const bool allowed = dev->suspended != 0u && dev->remote_wakeup != 0u;
    if (allowed) {
        usb_hal_remote_wakeup(&dev->hal);
    }
    usb_port_exit_critical(irq_state);
    return allowed ? USB_OK : USB_ERR_STATE;
}

/* ---- Endpoints for class drivers ---------------------------------------------- */

usb_status_t usbd_ep_open_ex(usbd_t *dev, const uint8_t *endpoint_desc, uint16_t transfer_size, uint8_t buffers)
{
    USB_CHECK(dev != NULL && endpoint_desc != NULL, USB_ERR_PARAM);
    USB_CHECK(endpoint_desc[1] == USB_DESC_TYPE_ENDPOINT, USB_ERR_PARAM);
    const uint8_t ep_addr = endpoint_desc[2];
    const usb_ep_type_t type = (usb_ep_type_t)(endpoint_desc[3] & 0x03u);
    const uint16_t max_packet = (uint16_t)(desc_u16(&endpoint_desc[4]) & 0x07FFu);
    const usb_status_t status = usb_hal_ep_open_ex(&dev->hal, ep_addr, type, max_packet,
                                                   transfer_size > max_packet ? transfer_size : max_packet, buffers);
    if (status == USB_OK) {
        dev->endpoint_owner[USB_EP_IS_IN(ep_addr) ? 1u : 0u][USB_EP_NUMBER(ep_addr)] = dev->opening;
    }
    return status;
}

usb_status_t usbd_ep_open(usbd_t *dev, const uint8_t *endpoint_desc)
{
    return usbd_ep_open_ex(dev, endpoint_desc, 0u, 1u);
}

usb_status_t usbd_ep_open_double(usbd_t *dev, const uint8_t *endpoint_desc)
{
    return usbd_ep_open_ex(dev, endpoint_desc, 0u, 2u);
}

usb_status_t usbd_ep_xfer(usbd_t *dev, uint8_t ep_addr, void *data, uint32_t length, bool zlp)
{
    return usb_hal_ep_xfer(&dev->hal, ep_addr, data, length, zlp);
}

volatile uint32_t *usbd_ep_buffer(const usbd_t *dev, uint8_t ep_addr)
{
    return usb_hal_ep_buffer(&dev->hal, ep_addr, NULL);
}

volatile uint32_t *usbd_ep_done_buffer(const usbd_t *dev, uint8_t ep_addr)
{
    return usb_hal_ep_done_buffer(&dev->hal, ep_addr);
}

usb_status_t usbd_ep_arm_raw(usbd_t *dev, uint8_t ep_addr, uint32_t length)
{
    return usb_hal_ep_arm_raw(&dev->hal, ep_addr, length);
}

bool usbd_ep_busy(const usbd_t *dev, uint8_t ep_addr)
{
    return usb_hal_ep_busy(&dev->hal, ep_addr);
}

usb_status_t usbd_ep_stall(usbd_t *dev, uint8_t ep_addr, bool stall)
{
    return usb_hal_ep_stall(&dev->hal, ep_addr, stall);
}

/* ---- Configurations --------------------------------------------------------- */

const uint8_t *usbd_active_configuration(const usbd_t *dev)
{
    return dev->configuration != 0u ? dev->descriptors->configurations[dev->config_index] : NULL;
}

void usbd_deconfigure(usbd_t *dev)
{
    usb_hal_ep_close_all(&dev->hal);
    if (dev->configuration != 0u) {
        for (uint32_t i = 0u; i < dev->num_classes; ++i) {
            if (dev->classes[i]->reset != NULL) {
                dev->classes[i]->reset(dev, dev->class_ctx[i]);
            }
        }
    }
    clear_owners(dev);
    dev->configuration = 0u;
}

/* Mark every interface descriptor in [desc, desc + length) as owned by @p owner. */
static void claim_interfaces(usbd_t *dev, const uint8_t *desc, uint32_t length, uint8_t owner)
{
    for (uint32_t i = 0u; i + 2u <= length && desc[i] != 0u; i += desc[i]) {
        if (desc[i + 1u] == USB_DESC_TYPE_INTERFACE && desc[i + 2u] < USBD_MAX_INTERFACES) {
            dev->interface_owner[desc[i + 2u]] = owner;
        }
    }
}

/* Offer the descriptors from @p desc on to each class driver; returns bytes consumed or 0. */
static uint16_t offer(usbd_t *dev, const uint8_t *desc, uint16_t length)
{
    for (uint32_t i = 0u; i < dev->num_classes; ++i) {
        dev->opening = (uint8_t)i;
        const uint16_t used = dev->classes[i]->open(dev, dev->class_ctx[i], desc, length);
        if (used > 0u) {
            claim_interfaces(dev, desc, used, (uint8_t)i);
            return used;
        }
    }
    return 0u;
}

usb_status_t usbd_configure(usbd_t *dev, uint8_t value)
{
    const usbd_descriptors_t *d = dev->descriptors;
    uint32_t index = 0u;
    while (index < d->num_configurations && d->configurations[index][5] != value) {
        ++index;
    }
    if (index == d->num_configurations) {
        return USB_ERR_PARAM;
    }
    usbd_deconfigure(dev);

    const uint8_t *config = d->configurations[index];
    const uint16_t total = desc_u16(&config[2]);
    uint16_t pos = config[0];
    while (pos + 2u <= total && config[pos] != 0u) {
        const uint8_t type = config[pos + 1u];
        const bool starts_function =
            type == USB_DESC_TYPE_INTERFACE_ASSOCIATION ||
            (type == USB_DESC_TYPE_INTERFACE && config[pos + 3u] == 0u &&
             (config[pos + 2u] >= USBD_MAX_INTERFACES || dev->interface_owner[config[pos + 2u]] == USBD_NO_OWNER));
        if (starts_function) {
            const uint16_t used = offer(dev, &config[pos], (uint16_t)(total - pos));
            if (used > 0u) {
                pos = (uint16_t)(pos + used);
                continue;
            }
        }
        pos = (uint16_t)(pos + config[pos]);
    }

    dev->configuration = value;
    dev->config_index = (uint8_t)index;
    return USB_OK;
}

usb_status_t usbd_set_interface(usbd_t *dev, uint8_t interface, uint8_t alternate)
{
    const uint8_t *config = usbd_active_configuration(dev);
    if (config == NULL || interface >= USBD_MAX_INTERFACES || dev->interface_owner[interface] == USBD_NO_OWNER) {
        return USB_ERR_STATE;
    }
    const uint16_t total = desc_u16(&config[2]);
    for (uint16_t pos = config[0]; pos + 4u <= total && config[pos] != 0u; pos = (uint16_t)(pos + config[pos])) {
        if (config[pos + 1u] == USB_DESC_TYPE_INTERFACE && config[pos + 2u] == interface && config[pos + 3u] == alternate) {
            const uint8_t owner = dev->interface_owner[interface];
            dev->opening = owner;
            if (dev->classes[owner]->open(dev, dev->class_ctx[owner], &config[pos], (uint16_t)(total - pos)) == 0u) {
                return USB_ERR_PARAM;
            }
            dev->interface_alt[interface] = alternate;
            return USB_OK;
        }
    }
    return USB_ERR_PARAM;
}

/* ---- Request routing -------------------------------------------------------- */

usbd_control_handler_t usbd_route(usbd_t *dev, const usb_setup_t *setup, void **ctx)
{
    const uint8_t type = setup->bmRequestType & USB_REQ_TYPE_MASK;
    const uint8_t recipient = setup->bmRequestType & USB_REQ_RECIPIENT_MASK;
    const uint8_t index = (uint8_t)(setup->wIndex & 0xFFu);
    uint8_t owner = USBD_NO_OWNER;
    *ctx = NULL;

    if (type == USB_REQ_TYPE_STANDARD) {
        const bool interface_std = setup->bRequest == USB_REQ_GET_STATUS ||
                                   setup->bRequest == USB_REQ_GET_INTERFACE ||
                                   setup->bRequest == USB_REQ_SET_INTERFACE;
        if (recipient != USB_REQ_RECIPIENT_INTERFACE || interface_std) {
            return usbd_std_request;
        }
    } else if (recipient == USB_REQ_RECIPIENT_DEVICE) {
        if (type == USB_REQ_TYPE_VENDOR) {
            *ctx = dev->app.ctx;
            return dev->app.vendor_request;
        }
        return NULL;
    }

    if (recipient == USB_REQ_RECIPIENT_INTERFACE && index < USBD_MAX_INTERFACES) {
        owner = dev->interface_owner[index];
    } else if (recipient == USB_REQ_RECIPIENT_ENDPOINT) {
        owner = dev->endpoint_owner[USB_EP_IS_IN(index) ? 1u : 0u][USB_EP_NUMBER(index)];
    }
    if (owner == USBD_NO_OWNER) {
        return NULL;
    }
    *ctx = dev->class_ctx[owner];
    return dev->classes[owner]->control;
}
