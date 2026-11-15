/**
 * @file main.c
 * @brief Example application: a CDC-ACM virtual COM port that sends back every byte
 *        it receives. Open the port with PuTTY, any terminal or pyserial.
 *
 * Integration with NEORV32: the USB core's irq is the machine external interrupt,
 * whose handler calls usbd_process(); that also follows VBUS (plug and unplug). The
 * main loop reports state changes on UART0 and echoes the data.
 */
#include <neorv32.h>

#include "platform.h"
#include "usb_descriptors.h"
#include "usb_port.h"
#include "usb_port_neorv32.h"
#include "usbd.h"
#include "usbd_cdc_acm.h"

static usbd_t usb;
static usbd_cdc_acm_t cdc;

static void usb_irq_handler(void)
{
    usbd_process(&usb);
}

static void report(void)
{
    static const char *const names[] = { "detached", "default", "address", "configured" };
    static usbd_state_t state = USBD_STATE_DETACHED;
    static bool port_open;
    static bool suspended;
    const usbd_state_t now = usbd_state(&usb);
    const bool open = usbd_cdc_acm_connected(&cdc);
    const bool asleep = usbd_suspended(&usb);
    if (now != state) {
        state = now;
        neorv32_uart0_printf("usb: %s\n", names[state]);
    }
    if (asleep != suspended) {
        suspended = asleep;
        /* Detaching ends a suspend too; that is no resume. */
        if (now != USBD_STATE_DETACHED) {
            neorv32_uart0_printf("usb: %s\n", asleep ? "suspended" : "resumed");
        }
    }
    if (open != port_open) {
        port_open = open;
        neorv32_uart0_printf("cdc: port %s\n", open ? "open" : "closed");
    }
}

int main(void)
{
    neorv32_rte_setup();
    neorv32_uart0_setup(19200, 0);

    usbd_cdc_acm_init(&cdc, NULL);
    if (usbd_init(&usb, USB_DEVICE_BASE, &app_descriptors, NULL) != USB_OK ||
        usbd_register_class(&usb, &usbd_cdc_acm_class, &cdc) != USB_OK) {
        neorv32_uart0_printf("usb: init failed\n");
        return 1;
    }

    usb_port_neorv32_fast_irq(usb_irq_handler);
    usb_port_irq_enable();
    neorv32_cpu_csr_set(CSR_MSTATUS, 1u << CSR_MSTATUS_MIE);
    usbd_connect(&usb, true);

    uint8_t buffer[64];
    for (;;) {
        report();
        const uint32_t n = usbd_cdc_acm_read(&cdc, buffer, sizeof buffer);
        for (uint32_t sent = 0u; sent < n;) {
            sent += usbd_cdc_acm_write(&cdc, &buffer[sent], n - sent);
        }
    }
}
