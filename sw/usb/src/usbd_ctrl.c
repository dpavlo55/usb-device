/**
 * @file usbd_ctrl.c
 * @brief Layer 3: control transfers on endpoint 0 (setup, data and status stages).
 *
 * The status OUT of a control read is armed together with the IN data stage, so a
 * host that ends the transfer early (Windows does after the first packet of the
 * device descriptor) is handled: the status packet completes the transfer and the
 * rest of the data is dropped. A new SETUP always aborts the transfer in progress;
 * hardware has disarmed EP0 by then.
 */
#include "usbd_internal.h"

static void stall(usbd_t *dev)
{
    usb_hal_ep0_stall(&dev->hal);
    dev->ctrl.state = CTRL_IDLE;
}

static void status_in(usbd_t *dev)
{
    dev->ctrl.state = CTRL_STATUS_IN;
    (void)usb_hal_ep_xfer(&dev->hal, 0x80u, NULL, 0u, false);
}

void usbd_ctrl_reset(usbd_t *dev)
{
    dev->ctrl.state = CTRL_IDLE;
    dev->ctrl.set_address = 0u;
}

void usbd_ctrl_send(usbd_t *dev, const void *data, uint16_t length)
{
    dev->ctrl.tx = (const uint8_t *)data;
    dev->ctrl.tx_length = length;
    dev->ctrl.answered = 1u;
}

void usbd_ctrl_receive(usbd_t *dev, void *buffer, uint16_t length)
{
    dev->ctrl.rx = (uint8_t *)buffer;
    dev->ctrl.rx_length = length;
    dev->ctrl.answered = 1u;
}

uint8_t *usbd_ctrl_buffer(usbd_t *dev)
{
    return dev->ctrl.buffer;
}

void usbd_ctrl_setup(usbd_t *dev, const uint8_t raw[8])
{
    usbd_ctrl_t *c = &dev->ctrl;
    usb_setup_parse(&c->setup, raw);
    c->state = CTRL_IDLE;
    c->answered = 0u;
    c->status_done = 0u;
    c->set_address = 0u;
    c->tx = NULL;
    c->tx_length = 0u;
    c->rx = NULL;
    c->rx_length = 0u;

    c->handler = usbd_route(dev, &c->setup, &c->handler_ctx);
    if (c->handler == NULL || !c->handler(dev, c->handler_ctx, USBD_CTRL_STAGE_SETUP, &c->setup)) {
        stall(dev);
        return;
    }

    const uint16_t requested = c->setup.wLength;
    if (requested == 0u) {
        status_in(dev);
    } else if ((c->setup.bmRequestType & USB_REQ_DIR_IN) != 0u) {
        if (!c->answered || c->tx == NULL) {
            stall(dev);
            return;
        }
        const uint16_t length = c->tx_length < requested ? c->tx_length : requested;
        c->state = CTRL_DATA_IN;
        (void)usb_hal_ep_xfer(&dev->hal, 0x00u, NULL, 0u, false);
        (void)usb_hal_ep_xfer(&dev->hal, 0x80u, (void *)c->tx, length, length < requested);
    } else {
        if (!c->answered || c->rx == NULL || c->rx_length < requested) {
            stall(dev);
            return;
        }
        c->state = CTRL_DATA_OUT;
        (void)usb_hal_ep_xfer(&dev->hal, 0x00u, c->rx, requested, false);
    }
}

void usbd_ctrl_xfer_done(usbd_t *dev, uint8_t ep_addr, uint32_t length)
{
    usbd_ctrl_t *c = &dev->ctrl;
    const bool in = USB_EP_IS_IN(ep_addr);
    (void)length;

    switch (c->state) {
    case CTRL_DATA_IN:
        if (in) {
            c->state = c->status_done ? CTRL_IDLE : CTRL_STATUS_OUT;
        } else {
            /* Status OUT, possibly before all data was taken: the transfer is over. */
            c->status_done = 1u;
            (void)usb_hal_ep_abort(&dev->hal, 0x80u);
            c->state = CTRL_IDLE;
        }
        break;
    case CTRL_STATUS_OUT:
        if (!in) {
            c->state = CTRL_IDLE;
        }
        break;
    case CTRL_DATA_OUT:
        if (!in) {
            if (c->handler(dev, c->handler_ctx, USBD_CTRL_STAGE_DATA, &c->setup)) {
                status_in(dev);
            } else {
                stall(dev);
            }
        }
        break;
    case CTRL_STATUS_IN:
        if (in) {
            c->state = CTRL_IDLE;
            if (c->set_address) {
                c->set_address = 0u;
                usb_hal_set_address(&dev->hal, c->address);
                usbd_set_state(dev, c->address != 0u ? USBD_STATE_ADDRESS : USBD_STATE_DEFAULT);
            }
        }
        break;
    default:
        break;
    }
}
