/**
 * @file usb_ram.h
 * @brief Layer 1: copies between CPU memory and the shared USB RAM.
 *
 * The RAM holds bytes as 32-bit little-endian words (byte i in bits 8*(i%4)+7..8*(i%4)
 * of word i/4). Every access to it is a full 32-bit access: a byte access costs as
 * much as a word access, so words move four times the data per bus cycle. RAM-side
 * addresses must be word-aligned (the HAL allocates buffers that way); CPU-side
 * buffers may have any alignment.
 */
#ifndef USB_RAM_H
#define USB_RAM_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/**
 * Copy @p len bytes from @p src to the RAM at word address @p dst.
 * The last word is written whole: bytes past @p len in it are unspecified, which is
 * harmless because hardware only sends the armed length.
 */
void usb_ram_write(volatile uint32_t *dst, const void *src, uint32_t len);

/** Copy @p len bytes from the RAM at word address @p src to @p dst. */
void usb_ram_read(void *dst, const volatile uint32_t *src, uint32_t len);

#ifdef __cplusplus
}
#endif

#endif /* USB_RAM_H */
