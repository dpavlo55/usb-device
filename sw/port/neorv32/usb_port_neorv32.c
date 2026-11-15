/**
 * @file usb_port_neorv32.c
 * @brief USB device library port for NEORV32: the core's irq is the machine
 *        external interrupt (mie.MEIE / mip.MEIP).
 *
 * The integrator installs a handler for TRAP_CODE_MEI that calls usbd_process(),
 * with the NEORV32 runtime or the lean entry usb_port_neorv32_fast_irq(), then sets
 * mstatus.MIE; see sw/app/cdc_echo/main.c.
 */
#include <neorv32.h>

#include "usb_port.h"
#include "usb_port_neorv32.h"

void usb_port_irq_enable(void)
{
    neorv32_cpu_csr_set(CSR_MIE, 1u << CSR_MIE_MEIE);
}

void usb_port_irq_disable(void)
{
    neorv32_cpu_csr_clr(CSR_MIE, 1u << CSR_MIE_MEIE);
}

/* Masks with mstatus.MIE, not mie.MEIE: clearing MEIE while the irq is already being
   taken makes NEORV32 enter the trap with no cause left, reported as a machine timer
   IRQ. mstatus.MIE gates the trap itself and leaves the cause intact. */
uint32_t usb_port_enter_critical(void)
{
    const uint32_t mstatus = neorv32_cpu_csr_read(CSR_MSTATUS);
    neorv32_cpu_csr_clr(CSR_MSTATUS, 1u << CSR_MSTATUS_MIE);
    return mstatus & (1u << CSR_MSTATUS_MIE);
}

void usb_port_exit_critical(uint32_t state)
{
    if (state != 0u) {
        neorv32_cpu_csr_set(CSR_MSTATUS, 1u << CSR_MSTATUS_MIE);
    }
}

static void (*fast_handler)(void);

void __attribute__((weak)) usb_port_neorv32_other_trap(uint32_t mcause)
{
    (void)mcause;
    for (;;) {
    }
}

static void __attribute__((interrupt("machine"), aligned(4))) fast_trap(void)
{
    const uint32_t mcause = neorv32_cpu_csr_read(CSR_MCAUSE);
    if (mcause == TRAP_CODE_MEI) {
        fast_handler();
    } else {
        usb_port_neorv32_other_trap(mcause);
    }
}

void usb_port_neorv32_fast_irq(void (*handler)(void))
{
    fast_handler = handler;
    neorv32_cpu_csr_write(CSR_MTVEC, (uint32_t)&fast_trap);
}
