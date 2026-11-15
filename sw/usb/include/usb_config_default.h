/**
 * @file usb_config_default.h
 * @brief Compile-time configuration of the USB device library.
 *
 * An application overrides any of these by providing its own `usb_config.h` on the
 * include path; it is picked up automatically when the compiler supports
 * `__has_include`, otherwise define the values on the command line.
 */
#ifndef USB_CONFIG_DEFAULT_H
#define USB_CONFIG_DEFAULT_H

#if defined(__has_include)
#if __has_include("usb_config.h")
#include "usb_config.h"
#endif
#endif

/** Check API parameters and return USB_ERR_PARAM on misuse (0 removes the checks). */
#ifndef USB_CONFIG_CHECK_PARAMS
#define USB_CONFIG_CHECK_PARAMS 1
#endif

/** Maximum packet size of endpoint 0: 8, 16, 32 or 64. */
#ifndef USB_EP0_SIZE
#define USB_EP0_SIZE 64u
#endif

/** Class drivers that can be registered with one device. */
#ifndef USBD_MAX_CLASSES
#define USBD_MAX_CLASSES 4u
#endif

/** Interfaces of the largest configuration. */
#ifndef USBD_MAX_INTERFACES
#define USBD_MAX_INTERFACES 8u
#endif

/**
 * Control transfer buffer: holds OUT data stages and descriptors the stack builds
 * (string descriptors). OUT data stages longer than this are refused with STALL.
 */
#ifndef USBD_CTRL_BUFFER_SIZE
#define USBD_CTRL_BUFFER_SIZE 256u
#endif

/** Profiling hook on the packet path, called with a point number 1..7 (empty by default). */
#ifndef USB_PROFILE
#define USB_PROFILE(point) ((void)0)
#endif

/** Called on internal inconsistencies; defaults to nothing. */
#ifndef USB_ASSERT
#define USB_ASSERT(condition) ((void)0)
#endif

#endif /* USB_CONFIG_DEFAULT_H */
