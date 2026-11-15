/**
 * @file usb_port.h
 * @brief Platform interface of the USB device library.
 *
 * The library contains no platform code. Whoever integrates the core implements
 * these functions for the target MCU and calls usbd_process() from the USB
 * interrupt (or from a task, or cyclically); see usbd.h.
 */
#ifndef USB_PORT_H
#define USB_PORT_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/** Unmask the USB interrupt line at the interrupt controller. */
void usb_port_irq_enable(void);

/** Mask the USB interrupt line at the interrupt controller. */
void usb_port_irq_disable(void);

/**
 * Keep usbd_process() from running until usb_port_exit_critical(), e.g. by masking
 * the USB interrupt. Used by API calls made from thread context while usbd_process()
 * may run in the interrupt.
 * @return state to hand back to usb_port_exit_critical()
 */
uint32_t usb_port_enter_critical(void);

/** Undo the matching usb_port_enter_critical(). */
void usb_port_exit_critical(uint32_t state);

#ifdef __cplusplus
}
#endif

#endif /* USB_PORT_H */
