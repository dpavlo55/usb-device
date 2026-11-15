/**
 * @file usb_hal.c
 * @brief Layer 2: endpoints, buffers and transfers of the USB device core.
 */
#include "usb_hal.h"

#include "usb_ram.h"

#define NO_ENTRY 0xFFu
#define RAW_TRANSFER 2u

#define EP_IRQ_DATA USB_DEVICE__ENDPOINTX__INTERRUPT_ENABLE__TRANSFER_COMPLETE_EN_bm
#define EP_IRQ_EP0_OUT (EP_IRQ_DATA | USB_DEVICE__ENDPOINTX__INTERRUPT_ENABLE__SETUP_EN_bm)

static uint32_t ll_kind(usb_ep_type_t type)
{
    switch (type) {
    case USB_EP_CONTROL:
        return USB_LL_KIND_CONTROL;
    case USB_EP_BULK:
        return USB_LL_KIND_BULK;
    case USB_EP_INTERRUPT:
        return USB_LL_KIND_INTERRUPT;
    default:
        return USB_LL_KIND_ISOCHRONOUS;
    }
}

/* Buffer size the core can express: a power of two from 8 to 1024 bytes. */
static uint32_t buffer_size_for(uint32_t max_packet, uint32_t *size_code)
{
    uint32_t size = 8u;
    uint32_t code = 0u;
    while (size < max_packet) {
        size <<= 1;
        ++code;
    }
    *size_code = code;
    return size;
}

static inline uint32_t entry_index(const usb_hal_t *hal, uint8_t ep_addr)
{
    return hal->entry_of[USB_EP_IS_IN(ep_addr) ? 1u : 0u][USB_EP_NUMBER(ep_addr)];
}

static inline volatile uint32_t *entry_ram(const usb_hal_t *hal, const usb_hal_entry_t *e)
{
    return hal->ram + (e->ram_offset >> 2);
}

/* Buffer @p turn of the endpoint on entry k: entry k itself, or entry k+1. */
static inline volatile uint32_t *turn_ram(const usb_hal_t *hal, uint32_t k, uint32_t turn)
{
    return entry_ram(hal, &hal->entry[k + turn]);
}

static inline uint32_t other_turn(const usb_hal_entry_t *e, uint32_t turn)
{
    return turn ^ (e->buffers - 1u);
}

static void write_control(usb_hal_t *hal, uint32_t k)
{
    usb_ll_ep_control(hal->base, k, hal->entry[k].control);
}

static void emit(usb_hal_t *hal, const usb_hal_event_t *event)
{
    if (hal->on_event != NULL) {
        hal->on_event(hal, event, hal->ctx);
    }
}

static void finish(usb_hal_t *hal, usb_hal_entry_t *e, uint32_t length)
{
    e->busy = e->armed != 0u ? RAW_TRANSFER : 0u;
    usb_hal_event_t event = { .type = USB_HAL_EVENT_XFER_DONE, .ep_addr = e->ep_addr, .length = length };
    emit(hal, &event);
}

/* ---- Endpoints ------------------------------------------------------------ */

static bool entry_free(const usb_hal_t *hal, uint32_t k)
{
    return k < USB_LL_ENTRIES && hal->entry[k].ep_addr == 0u;
}

static usb_status_t open_endpoint(usb_hal_t *hal, uint8_t ep_addr, usb_ep_type_t type, uint16_t max_packet,
                                  uint32_t transfer_size, uint32_t buffers)
{
    USB_CHECK(hal != NULL, USB_ERR_PARAM);
    USB_CHECK((ep_addr & 0x70u) == 0u, USB_ERR_PARAM);
    USB_CHECK(max_packet > 0u && max_packet <= 1023u, USB_ERR_PARAM);
    USB_CHECK(transfer_size <= 1024u, USB_ERR_PARAM);
    USB_CHECK(USB_EP_NUMBER(ep_addr) != 0u || type == USB_EP_CONTROL, USB_ERR_PARAM);
    USB_CHECK(buffers == 1u || type != USB_EP_CONTROL, USB_ERR_PARAM);

    const uint32_t dir_in = USB_EP_IS_IN(ep_addr) ? 1u : 0u;
    const uint32_t number = USB_EP_NUMBER(ep_addr);
    uint32_t packet_code;
    const uint32_t packet = buffer_size_for(max_packet, &packet_code);
    uint32_t size_code;
    const uint32_t size = buffer_size_for(transfer_size > max_packet ? transfer_size : max_packet, &size_code);
    const uint32_t split = size_code - packet_code;
    /* Hardware splits IN transfers into packets of a power of two bytes. */
    USB_CHECK(split == 0u || (dir_in && packet == max_packet && type != USB_EP_CONTROL), USB_ERR_PARAM);

    uint32_t k = hal->entry_of[dir_in][number];
    if (k == NO_ENTRY) {
        if (number == 0u) {
            k = dir_in ? USB_HAL_ENTRY_EP0_IN : USB_HAL_ENTRY_EP0_OUT;
        } else {
            for (k = USB_HAL_ENTRY_EP0_IN + 1u; k < USB_LL_ENTRIES; ++k) {
                if (entry_free(hal, k) && (buffers == 1u || entry_free(hal, k + 1u))) {
                    break;
                }
            }
            if (k == USB_LL_ENTRIES) {
                return USB_ERR_NO_RESOURCE;
            }
        }
    } else if (hal->entry[k].buffers != buffers) {
        return USB_ERR_PARAM;
    }

    usb_hal_entry_t *e = &hal->entry[k];
    usb_hal_entry_t *partner = &hal->entry[k + buffers - 1u];
    if (e->buffer_size < size) {
        if (hal->ram_next + buffers * size > hal->ram_size) {
            return USB_ERR_NO_RESOURCE;
        }
        e->ram_offset = (uint16_t)hal->ram_next;
        partner->ram_offset = (uint16_t)(hal->ram_next + (buffers - 1u) * size);
        e->buffer_size = (uint16_t)size;
        partner->buffer_size = (uint16_t)size;
        hal->ram_next += buffers * size;
    }

    hal->entry_of[dir_in][number] = (uint8_t)k;
    e->ep_addr = ep_addr;  /* entries 2..15 never serve endpoint 0, so non-zero marks them allocated */
    e->type = (uint8_t)type;
    e->max_packet = max_packet;
    e->open = 1u;
    e->busy = 0u;
    e->buffers = (uint8_t)buffers;
    e->transfer_size = (uint16_t)(split != 0u ? size : max_packet);
    e->arm_turn = 0u;
    e->done_turn = 0u;
    e->armed = 0u;
    e->control = usb_ll_ep_control_word(e->ram_offset, number, 0u, size_code, ll_kind(type), dir_in ? 0u : 1u, 1u) |
                 (split << USB_DEVICE__ENDPOINTX__CONTROL__SPLIT_bp);
    if (buffers == 2u) {
        /* Disabling the entry returns hardware to the first buffer. */
        e->control |= USB_DEVICE__ENDPOINTX__CONTROL__DOUBLE_BUFFER_bm;
        usb_ll_ep_control(hal->base, k, e->control & ~USB_DEVICE__ENDPOINTX__CONTROL__ENABLE_bm);
        partner->ep_addr = ep_addr;
        partner->type = (uint8_t)type;
        partner->max_packet = max_packet;
        partner->open = 1u;
        partner->buffers = 0u;
        partner->control = usb_ll_ep_control_word(partner->ram_offset, number, 0u, size_code, ll_kind(type),
                                                  dir_in ? 0u : 1u, 1u) |
                           (split << USB_DEVICE__ENDPOINTX__CONTROL__SPLIT_bp);
        write_control(hal, k + 1u);
        usb_ll_ep_irq_enable(hal->base, k + 1u, EP_IRQ_DATA);
    }
    write_control(hal, k);
    usb_ll_ep_reset_toggle(hal->base, k);
    usb_ll_ep_irq_enable(hal->base, k, k == USB_HAL_ENTRY_EP0_OUT ? EP_IRQ_EP0_OUT : EP_IRQ_DATA);
    return USB_OK;
}

usb_status_t usb_hal_ep_open(usb_hal_t *hal, uint8_t ep_addr, usb_ep_type_t type, uint16_t max_packet)
{
    return open_endpoint(hal, ep_addr, type, max_packet, max_packet, 1u);
}

usb_status_t usb_hal_ep_open_double(usb_hal_t *hal, uint8_t ep_addr, usb_ep_type_t type, uint16_t max_packet)
{
    return open_endpoint(hal, ep_addr, type, max_packet, max_packet, 2u);
}

usb_status_t usb_hal_ep_open_ex(usb_hal_t *hal, uint8_t ep_addr, usb_ep_type_t type, uint16_t max_packet,
                                uint16_t transfer_size, uint8_t buffers)
{
    USB_CHECK(buffers == 1u || buffers == 2u, USB_ERR_PARAM);
    return open_endpoint(hal, ep_addr, type, max_packet, transfer_size, buffers);
}

usb_status_t usb_hal_ep_close(usb_hal_t *hal, uint8_t ep_addr)
{
    USB_CHECK(hal != NULL, USB_ERR_PARAM);
    const uint32_t k = entry_index(hal, ep_addr);
    USB_CHECK(k != NO_ENTRY, USB_ERR_STATE);
    usb_hal_entry_t *e = &hal->entry[k];
    e->busy = 0u;
    for (uint32_t turn = 0u; turn < e->buffers; ++turn) {
        hal->entry[k + turn].open = 0u;
        hal->entry[k + turn].control &= ~(USB_DEVICE__ENDPOINTX__CONTROL__ENABLE_bm |
                                          USB_DEVICE__ENDPOINTX__CONTROL__STALL_bm);
        write_control(hal, k + turn);
    }
    return USB_OK;
}

void usb_hal_ep_close_all(usb_hal_t *hal)
{
    for (uint32_t k = USB_HAL_ENTRY_EP0_IN + 1u; k < USB_LL_ENTRIES; ++k) {
        usb_hal_entry_t *e = &hal->entry[k];
        if (e->ep_addr != 0u) {
            hal->entry_of[USB_EP_IS_IN(e->ep_addr) ? 1u : 0u][USB_EP_NUMBER(e->ep_addr)] = NO_ENTRY;
        }
        e->control = 0u;
        write_control(hal, k);
        (void)usb_ll_ep_irq_take(hal->base, k);
        *e = (usb_hal_entry_t){ 0 };
    }
    hal->ram_next = hal->ram_reserved;
}

static usb_hal_entry_t *open_entry(usb_hal_t *hal, uint8_t ep_addr, uint32_t *k)
{
    *k = entry_index(hal, ep_addr);
    if (*k == NO_ENTRY || !hal->entry[*k].open) {
        return NULL;
    }
    return &hal->entry[*k];
}

/* Hand the buffer whose turn it is to hardware. */
static void arm_next(usb_hal_t *hal, uint32_t k, usb_hal_entry_t *e, uint32_t length)
{
    const uint32_t turn = e->arm_turn;
    e->flight[turn] = (uint16_t)length;
    e->arm_turn = (uint8_t)other_turn(e, turn);
    ++e->armed;
    usb_ll_ep_arm(hal->base, k + turn, USB_EP_IS_IN(e->ep_addr) ? length : 0u);
}

/* Arm the next IN packet of the transfer: the next chunk, or the closing ZLP. */
static void in_next(usb_hal_t *hal, uint32_t k, usb_hal_entry_t *e)
{
    uint32_t chunk = e->length - e->done;
    if (chunk > e->transfer_size) {
        chunk = e->transfer_size;
    }
    if (chunk > 0u) {
        usb_ram_write(turn_ram(hal, k, e->arm_turn), e->data + e->done, chunk);
    }
    arm_next(hal, k, e, chunk);
}

usb_status_t usb_hal_ep_xfer(usb_hal_t *hal, uint8_t ep_addr, void *data, uint32_t length, bool zlp)
{
    USB_CHECK(hal != NULL, USB_ERR_PARAM);
    USB_CHECK(data != NULL || length == 0u, USB_ERR_PARAM);
    uint32_t k;
    usb_hal_entry_t *e = open_entry(hal, ep_addr, &k);
    USB_CHECK(e != NULL, USB_ERR_STATE);
    if (e->busy) {
        return USB_ERR_BUSY;
    }
    e->busy = 1u;
    e->data = (uint8_t *)data;
    e->length = length;
    e->done = 0u;
    e->zlp = zlp ? 1u : 0u;
    if (USB_EP_IS_IN(ep_addr)) {
        in_next(hal, k, e);
    } else {
        arm_next(hal, k, e, 0u);
    }
    return USB_OK;
}

usb_status_t usb_hal_ep_abort(usb_hal_t *hal, uint8_t ep_addr)
{
    USB_CHECK(hal != NULL, USB_ERR_PARAM);
    const uint32_t k = entry_index(hal, ep_addr);
    USB_CHECK(k != NO_ENTRY, USB_ERR_STATE);
    hal->entry[k].busy = 0u;
    return USB_OK;
}

bool usb_hal_ep_busy(const usb_hal_t *hal, uint8_t ep_addr)
{
    const uint32_t k = entry_index(hal, ep_addr);
    return k != NO_ENTRY && hal->entry[k].busy != 0u;
}

usb_status_t usb_hal_ep_stall(usb_hal_t *hal, uint8_t ep_addr, bool stall)
{
    USB_CHECK(hal != NULL, USB_ERR_PARAM);
    uint32_t k;
    usb_hal_entry_t *e = open_entry(hal, ep_addr, &k);
    USB_CHECK(e != NULL, USB_ERR_STATE);
    if (stall) {
        e->control |= USB_DEVICE__ENDPOINTX__CONTROL__STALL_bm;
    } else {
        e->control &= ~USB_DEVICE__ENDPOINTX__CONTROL__STALL_bm;
    }
    write_control(hal, k);
    if (!stall) {
        usb_ll_ep_reset_toggle(hal->base, k);
    }
    return USB_OK;
}

bool usb_hal_ep_stalled(const usb_hal_t *hal, uint8_t ep_addr)
{
    const uint32_t k = entry_index(hal, ep_addr);
    return k != NO_ENTRY && (hal->entry[k].control & USB_DEVICE__ENDPOINTX__CONTROL__STALL_bm) != 0u;
}

void usb_hal_ep0_stall(usb_hal_t *hal)
{
    for (uint32_t k = USB_HAL_ENTRY_EP0_OUT; k <= USB_HAL_ENTRY_EP0_IN; ++k) {
        hal->entry[k].busy = 0u;
        hal->entry[k].control |= USB_DEVICE__ENDPOINTX__CONTROL__STALL_bm;
        write_control(hal, k);
    }
}

volatile uint32_t *usb_hal_ep_buffer(const usb_hal_t *hal, uint8_t ep_addr, uint32_t *size)
{
    const uint32_t k = entry_index(hal, ep_addr);
    if (k == NO_ENTRY) {
        return NULL;
    }
    if (size != NULL) {
        *size = hal->entry[k].buffer_size;
    }
    return turn_ram(hal, k, hal->entry[k].arm_turn);
}

volatile uint32_t *usb_hal_ep_done_buffer(const usb_hal_t *hal, uint8_t ep_addr)
{
    const uint32_t k = entry_index(hal, ep_addr);
    if (k == NO_ENTRY) {
        return NULL;
    }
    const usb_hal_entry_t *e = &hal->entry[k];
    return turn_ram(hal, k, other_turn(e, e->done_turn));
}

usb_status_t usb_hal_ep_arm_raw(usb_hal_t *hal, uint8_t ep_addr, uint32_t length)
{
    USB_CHECK(hal != NULL, USB_ERR_PARAM);
    uint32_t k;
    usb_hal_entry_t *e = open_entry(hal, ep_addr, &k);
    USB_CHECK(e != NULL, USB_ERR_STATE);
    USB_CHECK(length <= e->transfer_size, USB_ERR_PARAM);
    if ((e->busy != 0u && e->busy != RAW_TRANSFER) || e->armed >= e->buffers) {
        return USB_ERR_BUSY;
    }
    e->busy = RAW_TRANSFER;
    arm_next(hal, k, e, length);
    return USB_OK;
}

/* ---- Device --------------------------------------------------------------- */

usb_status_t usb_hal_init(usb_hal_t *hal, uintptr_t base, usb_hal_event_cb_t on_event, void *ctx)
{
    USB_CHECK(hal != NULL, USB_ERR_PARAM);
    *hal = (usb_hal_t){ 0 };
    hal->base = base;
    hal->ram = (volatile uint32_t *)usb_ll_ram(base);
    hal->ram_size = usb_ll_ram_size(base);
    hal->on_event = on_event;
    hal->ctx = ctx;
    for (uint32_t d = 0u; d < 2u; ++d) {
        for (uint32_t n = 0u; n < 16u; ++n) {
            hal->entry_of[d][n] = NO_ENTRY;
        }
    }
    for (uint32_t k = 0u; k < USB_LL_ENTRIES; ++k) {
        usb_ll_ep_control(base, k, 0u);
        (void)usb_ll_ep_irq_take(base, k);
    }
    (void)usb_ll_bus_event_take(base);
    usb_ll_device_address(base, 0u);

    hal->control = usb_ll_control_word(1u, 0u);
    usb_ll_control(base, hal->control);

    usb_status_t status = usb_hal_ep_open(hal, 0x00u, USB_EP_CONTROL, USB_EP0_SIZE);
    if (status == USB_OK) {
        status = usb_hal_ep_open(hal, 0x80u, USB_EP_CONTROL, USB_EP0_SIZE);
    }
    hal->ram_reserved = hal->ram_next;

    usb_ll_bus_event_enable(base, USB_DEVICE__BUS_EVENT_ENABLE__VBUS_EN_bm |
                                  USB_DEVICE__BUS_EVENT_ENABLE__BUS_RESET_EN_bm |
                                  USB_DEVICE__BUS_EVENT_ENABLE__SUSPEND_EN_bm |
                                  USB_DEVICE__BUS_EVENT_ENABLE__RESUME_EN_bm);
    usb_ll_irq_enable(base, USB_LL_IRQ_ALL);
    return status;
}

void usb_hal_connect(usb_hal_t *hal, bool connect)
{
    hal->control = usb_ll_control_word(1u, connect ? 1u : 0u);
    usb_ll_control(hal->base, hal->control);
}

bool usb_hal_vbus_present(const usb_hal_t *hal)
{
    return usb_ll_vbus_present(hal->base) != 0u;
}

bool usb_hal_suspended(const usb_hal_t *hal)
{
    return (usb_ll_status(hal->base) & USB_DEVICE__STATUS__SUSPENDED_bm) != 0u;
}

uint16_t usb_hal_frame_number(const usb_hal_t *hal)
{
    return (uint16_t)usb_ll_frame_number(hal->base);
}

void usb_hal_remote_wakeup(usb_hal_t *hal)
{
    usb_ll_remote_wakeup(hal->base, hal->control);
}

void usb_hal_set_address(usb_hal_t *hal, uint8_t address)
{
    usb_ll_device_address(hal->base, address);
}

/* ---- Events --------------------------------------------------------------- */

/* Hardware has reset the toggles. EP0 interrupt causes and toggles are left alone: a
   SETUP may already have arrived after the reset ended, and stale completions are
   ignored because no transfer is busy. */
static void emit_bus_event(usb_hal_t *hal, usb_hal_event_type_t type)
{
    usb_hal_event_t event = { .type = type };
    emit(hal, &event);
}

static void on_bus_reset(usb_hal_t *hal)
{
    usb_hal_ep_close_all(hal);
    for (uint32_t k = USB_HAL_ENTRY_EP0_OUT; k <= USB_HAL_ENTRY_EP0_IN; ++k) {
        hal->entry[k].busy = 0u;
        hal->entry[k].control &= ~USB_DEVICE__ENDPOINTX__CONTROL__STALL_bm;
        write_control(hal, k);
    }
    usb_hal_event_t event = { .type = USB_HAL_EVENT_BUS_RESET };
    emit(hal, &event);
}

/* Hardware cleared stall and armed on both EP0 entries; the old control transfer is gone. */
static void on_setup(usb_hal_t *hal)
{
    usb_hal_event_t event = { .type = USB_HAL_EVENT_SETUP };
    const usb_hal_entry_t *out = &hal->entry[USB_HAL_ENTRY_EP0_OUT];
    usb_ram_read(event.setup, entry_ram(hal, out), sizeof event.setup);
    for (uint32_t k = USB_HAL_ENTRY_EP0_OUT; k <= USB_HAL_ENTRY_EP0_IN; ++k) {
        hal->entry[k].busy = 0u;
        hal->entry[k].control &= ~USB_DEVICE__ENDPOINTX__CONTROL__STALL_bm;
    }
    emit(hal, &event);
}

/* The packet in the buffer whose turn it is to complete has finished. */
static void on_complete(usb_hal_t *hal, uint32_t k)
{
    usb_hal_entry_t *e = &hal->entry[k];
    if (!e->busy) {
        return;
    }
    const bool in = USB_EP_IS_IN(e->ep_addr);
    const uint32_t turn = e->done_turn;
    e->done_turn = (uint8_t)other_turn(e, turn);
    if (e->armed > 0u) {
        --e->armed;
    }

    if (e->busy == RAW_TRANSFER) {
        const uint32_t length = in ? e->flight[turn] : usb_ll_ep_byte_count(usb_ll_ep_status(hal->base, k + turn));
        USB_PROFILE(3);
        finish(hal, e, length);
        return;
    }

    if (in) {
        const uint32_t sent = e->flight[turn];
        e->done += sent;
        if (e->done < e->length) {
            in_next(hal, k, e);
        } else if (e->zlp && sent != 0u && sent % e->max_packet == 0u) {
            e->zlp = 0u;
            in_next(hal, k, e);
        } else {
            finish(hal, e, e->done);
        }
        return;
    }

    const uint32_t received = usb_ll_ep_byte_count(usb_ll_ep_status(hal->base, k + turn));
    uint32_t take = e->length - e->done;
    if (take > received) {
        take = received;
    }
    if (take > 0u) {
        usb_ram_read(e->data + e->done, turn_ram(hal, k, turn), take);
    }
    e->done += take;
    if (received < e->max_packet || e->done >= e->length) {
        finish(hal, e, e->done);
    } else {
        arm_next(hal, k, e, 0u);
    }
}

/* Completions of a double-buffered endpoint, in the order hardware used its buffers. */
static void on_pair_complete(usb_hal_t *hal, uint32_t k, uint32_t causes[2])
{
    usb_hal_entry_t *e = &hal->entry[k];
    for (uint32_t n = 0u; n < 2u; ++n) {
        uint32_t turn = e->done_turn;
        if ((causes[turn] & USB_DEVICE__ENDPOINTX__INTERRUPT__TRANSFER_COMPLETE_bm) == 0u) {
            turn ^= 1u;
            if ((causes[turn] & USB_DEVICE__ENDPOINTX__INTERRUPT__TRANSFER_COMPLETE_bm) == 0u) {
                return;
            }
            e->done_turn = (uint8_t)turn;
        }
        causes[turn] = 0u;
        on_complete(hal, k);
    }
}

void usb_hal_process(usb_hal_t *hal)
{
    uint32_t pending = usb_ll_irq_pending(hal->base);
    USB_PROFILE(1);
    while (pending != 0u) {
        if ((pending & USB_DEVICE__INTERRUPT__BUS_EVENT_bm) != 0u) {
            /* In the order they happen: a bus reset during suspend first ends the suspend. */
            const uint32_t events = usb_ll_bus_event_take(hal->base);
            if ((events & USB_DEVICE__BUS_EVENT__VBUS_bm) != 0u) {
                emit_bus_event(hal, USB_HAL_EVENT_VBUS);
            }
            if ((events & USB_DEVICE__BUS_EVENT__SUSPEND_bm) != 0u) {
                emit_bus_event(hal, USB_HAL_EVENT_SUSPEND);
            }
            if ((events & USB_DEVICE__BUS_EVENT__RESUME_bm) != 0u) {
                emit_bus_event(hal, USB_HAL_EVENT_RESUME);
            }
            if ((events & USB_DEVICE__BUS_EVENT__BUS_RESET_bm) != 0u) {
                on_bus_reset(hal);
                pending = usb_ll_irq_pending(hal->base);
                continue;
            }
        }
        uint32_t entries = pending & 0xFFFFu;
        while (entries != 0u) {
            const uint32_t k = usb_ctz32(entries);
            entries &= entries - 1u;
            const uint32_t causes = usb_ll_ep_irq_take(hal->base, k);
            USB_PROFILE(2);
            const uint32_t leader = (k > 0u && hal->entry[k].buffers == 0u && hal->entry[k - 1u].buffers == 2u)
                                        ? k - 1u : k;
            if (hal->entry[leader].buffers == 2u) {
                const uint32_t other = k == leader ? k + 1u : leader;
                uint32_t pair_causes[2];
                entries &= ~(1u << other);
                pair_causes[k - leader] = causes;
                pair_causes[other - leader] = (pending >> other) & 1u ? usb_ll_ep_irq_take(hal->base, other) : 0u;
                on_pair_complete(hal, leader, pair_causes);
                continue;
            }
            if ((causes & USB_DEVICE__ENDPOINTX__INTERRUPT__TRANSFER_COMPLETE_bm) != 0u) {
                on_complete(hal, k);
            }
            if ((causes & USB_DEVICE__ENDPOINTX__INTERRUPT__SETUP_bm) != 0u) {
                /* An EP0 IN completion still pending belongs to the aborted transfer. */
                (void)usb_ll_ep_irq_take(hal->base, USB_HAL_ENTRY_EP0_IN);
                entries &= ~(1u << USB_HAL_ENTRY_EP0_IN);
                on_setup(hal);
            }
        }
        pending = usb_ll_irq_pending(hal->base);
    }
    USB_PROFILE(7);
}
