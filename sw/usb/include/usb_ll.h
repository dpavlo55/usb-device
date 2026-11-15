/**
 * @file usb_ll.h
 * @brief Layer 1: register access to the USB device core.
 *
 * One function per register action, no policy and no state. Register addresses
 * and field positions come from the generated usb_device_regs.h (rdl/usb_device.rdl).
 *
 * Every access is a single full-word access through a volatile uint32_t pointer:
 * the generated struct is packed, and member accesses through it may be split into
 * byte accesses. Writes never read-modify-write: `interrupt` and `bus_event` clear
 * on writing 1, `stall` and `armed` are also changed by hardware, and `arm`,
 * `reset_toggle` and `remote_wakeup` are pulses. Callers keep shadow copies of the
 * registers they own (see usb_hal.c).
 */
#ifndef USB_LL_H
#define USB_LL_H

#include <stddef.h>
#include <stdint.h>

#include "usb_device_regs.h"

#ifdef __cplusplus
extern "C" {
#endif

/** Number of endpoint entries of the core. */
#define USB_LL_ENTRIES 16u

/** Endpoint kinds as the core encodes them (endpoint_kind_e). */
#define USB_LL_KIND_CONTROL 0u
#define USB_LL_KIND_BULK 1u
#define USB_LL_KIND_INTERRUPT 2u
#define USB_LL_KIND_ISOCHRONOUS 3u

#define USB_LL_REG(base, member) \
    (*(volatile uint32_t *)((base) + offsetof(usb_device_t, member)))

#define USB_LL_EP_REG(base, entry, member)                                      \
    (*(volatile uint32_t *)((base) + offsetof(usb_device_t, endpoint) +         \
                            (uintptr_t)(entry) * sizeof(usb_device__endpointx_t) + \
                            offsetof(usb_device__endpointx_t, member)))

/** Address of the shared RAM of the core at @p base. */
static inline uintptr_t usb_ll_ram(uintptr_t base)
{
    return base + offsetof(usb_device_t, usb_ram);
}

/* ---- Identification and status ------------------------------------------- */

static inline uint32_t usb_ll_core_id(uintptr_t base)
{
    return USB_LL_REG(base, core_id);
}

static inline uint32_t usb_ll_status(uintptr_t base)
{
    return USB_LL_REG(base, status);
}

/** Size of the shared RAM in bytes (status.ram_words counts 32-bit words). */
static inline uint32_t usb_ll_ram_size(uintptr_t base)
{
    return ((usb_ll_status(base) & USB_DEVICE__STATUS__RAM_WORDS_bm) >> USB_DEVICE__STATUS__RAM_WORDS_bp) * 4u;
}

/** Frame number of the last good SOF (11 bits; 0 after reset, held during suspend). */
static inline uint32_t usb_ll_frame_number(uintptr_t base)
{
    return (usb_ll_status(base) & USB_DEVICE__STATUS__FRAME_NUMBER_bm) >> USB_DEVICE__STATUS__FRAME_NUMBER_bp;
}

static inline uint32_t usb_ll_vbus_present(uintptr_t base)
{
    return (usb_ll_status(base) & USB_DEVICE__STATUS__VBUS_PRESENT_bm) >> USB_DEVICE__STATUS__VBUS_PRESENT_bp;
}

/* ---- Global control -------------------------------------------------------- */

/** control word from its fields: core enable and D+ pull-up. */
static inline uint32_t usb_ll_control_word(uint32_t enable_core, uint32_t pull_up)
{
    return ((enable_core << USB_DEVICE__CONTROL__ENABLE_CORE_bp) & USB_DEVICE__CONTROL__ENABLE_CORE_bm) |
           ((pull_up << USB_DEVICE__CONTROL__ENABLE_PU_bp) & USB_DEVICE__CONTROL__ENABLE_PU_bm);
}

static inline void usb_ll_control(uintptr_t base, uint32_t word)
{
    USB_LL_REG(base, control) = word;
}

/** Pulse remote wakeup; @p word is the current control word (kept by the caller). */
static inline void usb_ll_remote_wakeup(uintptr_t base, uint32_t word)
{
    USB_LL_REG(base, control) = word | USB_DEVICE__CONTROL__REMOTE_WAKEUP_bm;
}

static inline void usb_ll_device_address(uintptr_t base, uint32_t address)
{
    USB_LL_REG(base, device_address) =
        (address << USB_DEVICE__DEVICE_ADDRESS__ADDRESS_bp) & USB_DEVICE__DEVICE_ADDRESS__ADDRESS_bm;
}

/* ---- Interrupts and bus events --------------------------------------------- */

/** All bits of the global interrupt register: 16 endpoints and bus events. */
#define USB_LL_IRQ_ALL ((uint32_t)0xFFFFu | USB_DEVICE__INTERRUPT__BUS_EVENT_bm)

static inline void usb_ll_irq_enable(uintptr_t base, uint32_t mask)
{
    USB_LL_REG(base, interrupt_enable) = mask;
}

/** Pending causes: bit n for endpoint entry n, plus USB_DEVICE__INTERRUPT__BUS_EVENT_bm. */
static inline uint32_t usb_ll_irq_pending(uintptr_t base)
{
    return USB_LL_REG(base, interrupt);
}

static inline void usb_ll_bus_event_enable(uintptr_t base, uint32_t mask)
{
    USB_LL_REG(base, bus_event_enable) = mask;
}

/** Read the pending bus events and clear exactly those. */
static inline uint32_t usb_ll_bus_event_take(uintptr_t base)
{
    const uint32_t events = USB_LL_REG(base, bus_event);
    USB_LL_REG(base, bus_event) = events;
    return events;
}

/* ---- Endpoint entries ------------------------------------------------------ */

/** Endpoint control word from its fields. */
static inline uint32_t usb_ll_ep_control_word(uint32_t base_address, uint32_t endpoint_number,
                                              uint32_t stall, uint32_t size_code, uint32_t kind,
                                              uint32_t direction_out, uint32_t enable)
{
    return ((base_address << USB_DEVICE__ENDPOINTX__CONTROL__BASE_ADDRESS_bp) & USB_DEVICE__ENDPOINTX__CONTROL__BASE_ADDRESS_bm) |
           ((endpoint_number << USB_DEVICE__ENDPOINTX__CONTROL__ENDPOINT_NUMBER_bp) & USB_DEVICE__ENDPOINTX__CONTROL__ENDPOINT_NUMBER_bm) |
           ((stall << USB_DEVICE__ENDPOINTX__CONTROL__STALL_bp) & USB_DEVICE__ENDPOINTX__CONTROL__STALL_bm) |
           ((size_code << USB_DEVICE__ENDPOINTX__CONTROL__SIZE_bp) & USB_DEVICE__ENDPOINTX__CONTROL__SIZE_bm) |
           ((kind << USB_DEVICE__ENDPOINTX__CONTROL__KIND_bp) & USB_DEVICE__ENDPOINTX__CONTROL__KIND_bm) |
           ((direction_out << USB_DEVICE__ENDPOINTX__CONTROL__DIRECTION_bp) & USB_DEVICE__ENDPOINTX__CONTROL__DIRECTION_bm) |
           ((enable << USB_DEVICE__ENDPOINTX__CONTROL__ENABLE_bp) & USB_DEVICE__ENDPOINTX__CONTROL__ENABLE_bm);
}

static inline void usb_ll_ep_control(uintptr_t base, uint32_t entry, uint32_t word)
{
    USB_LL_EP_REG(base, entry, control) = word;
}

/** Hand the buffer to hardware: OUT receives one packet, IN sends @p length bytes. */
static inline void usb_ll_ep_arm(uintptr_t base, uint32_t entry, uint32_t length)
{
    USB_LL_EP_REG(base, entry, transfer) =
        USB_DEVICE__ENDPOINTX__TRANSFER__ARM_bm |
        ((length << USB_DEVICE__ENDPOINTX__TRANSFER__LENGTH_bp) & USB_DEVICE__ENDPOINTX__TRANSFER__LENGTH_bm);
}

/** Return the data toggle to DATA0. */
static inline void usb_ll_ep_reset_toggle(uintptr_t base, uint32_t entry)
{
    USB_LL_EP_REG(base, entry, transfer) = USB_DEVICE__ENDPOINTX__TRANSFER__RESET_TOGGLE_bm;
}

static inline uint32_t usb_ll_ep_status(uintptr_t base, uint32_t entry)
{
    return USB_LL_EP_REG(base, entry, status);
}

static inline uint32_t usb_ll_ep_byte_count(uint32_t status)
{
    return (status & USB_DEVICE__ENDPOINTX__STATUS__BYTE_COUNT_bm) >> USB_DEVICE__ENDPOINTX__STATUS__BYTE_COUNT_bp;
}

static inline uint32_t usb_ll_ep_armed(uint32_t status)
{
    return (status & USB_DEVICE__ENDPOINTX__STATUS__ARMED_bm) >> USB_DEVICE__ENDPOINTX__STATUS__ARMED_bp;
}

static inline void usb_ll_ep_irq_enable(uintptr_t base, uint32_t entry, uint32_t mask)
{
    USB_LL_EP_REG(base, entry, interrupt_enable) = mask;
}

/** Read the pending causes of an entry and clear exactly those. */
static inline uint32_t usb_ll_ep_irq_take(uintptr_t base, uint32_t entry)
{
    const uint32_t causes = USB_LL_EP_REG(base, entry, interrupt);
    USB_LL_EP_REG(base, entry, interrupt) = causes;
    return causes;
}

#ifdef __cplusplus
}
#endif

#endif /* USB_LL_H */
