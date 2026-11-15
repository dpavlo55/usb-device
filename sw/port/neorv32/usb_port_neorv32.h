/**
 * @file usb_port_neorv32.h
 * @brief NEORV32-specific additions of the USB device library port.
 */
#ifndef USB_PORT_NEORV32_H
#define USB_PORT_NEORV32_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/**
 * Take over the trap vector (mtvec) with a lean entry that calls @p handler for the
 * machine external interrupt (the USB core's irq). It saves only the registers the
 * calling convention lets @p handler clobber, about half the cost of the NEORV32
 * runtime's entry, which saves all of them and dispatches through a table.
 * Every other trap calls usb_port_neorv32_other_trap(). Call it after
 * neorv32_rte_setup(); handlers installed with the runtime no longer run.
 */
void usb_port_neorv32_fast_irq(void (*handler)(void));

/**
 * Any trap other than the machine external interrupt while the lean entry is in
 * use. The default halts; an application may override it (a weak symbol). For an
 * exception, returning resumes at the trapping instruction unless mepc is advanced.
 */
void usb_port_neorv32_other_trap(uint32_t mcause);

#ifdef __cplusplus
}
#endif

#endif /* USB_PORT_NEORV32_H */
