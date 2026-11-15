/**
 * @file usb_common.h
 * @brief Types and compiler helpers shared by every layer of the USB device library.
 */
#ifndef USB_COMMON_H
#define USB_COMMON_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "usb_config_default.h"

#ifdef __cplusplus
extern "C" {
#endif

/** Result of a library call. */
typedef enum {
    USB_OK = 0,           /**< Done. */
    USB_ERR_PARAM,        /**< Invalid argument. */
    USB_ERR_BUSY,         /**< The endpoint already has a transfer in progress. */
    USB_ERR_NO_RESOURCE,  /**< No free endpoint entry, buffer RAM or table slot. */
    USB_ERR_STATE         /**< Not allowed in the current state (e.g. endpoint not open). */
} usb_status_t;

#if defined(__GNUC__) || defined(__clang__)
#define USB_MAY_ALIAS __attribute__((__may_alias__))
#define USB_LIKELY(x) __builtin_expect(!!(x), 1)
#define USB_UNLIKELY(x) __builtin_expect(!!(x), 0)
#define USB_ALIGNED(n) __attribute__((__aligned__(n)))
#else
#define USB_MAY_ALIAS
#define USB_LIKELY(x) (x)
#define USB_UNLIKELY(x) (x)
#define USB_ALIGNED(n)
#endif

#if defined(__BYTE_ORDER__) && defined(__ORDER_LITTLE_ENDIAN__) && (__BYTE_ORDER__ == __ORDER_LITTLE_ENDIAN__)
#define USB_CPU_LITTLE_ENDIAN 1
#else
#define USB_CPU_LITTLE_ENDIAN 0
#endif

/** A 32-bit word that may alias any object, for word-wise copies of byte buffers. */
typedef uint32_t USB_MAY_ALIAS usb_word_t;

/** Index of the lowest set bit; @p x must not be 0. */
static inline uint32_t usb_ctz32(uint32_t x)
{
#if defined(__GNUC__) || defined(__clang__)
    return (uint32_t)__builtin_ctz(x);
#else
    uint32_t n = 0u;
    while ((x & 1u) == 0u) {
        x >>= 1;
        ++n;
    }
    return n;
#endif
}

#if USB_CONFIG_CHECK_PARAMS
#define USB_CHECK(condition, error) \
    do {                            \
        if (!(condition)) {         \
            return (error);         \
        }                           \
    } while (0)
#else
#define USB_CHECK(condition, error) ((void)0)
#endif

#ifdef __cplusplus
}
#endif

#endif /* USB_COMMON_H */
