#ifndef TEST_STATUS_H
#define TEST_STATUS_H

#include <stdint.h>

/* Simulation-only XBUS register in usb_top_tb. 0 means still running. */
static inline void test_finish(uint32_t status) {
    *(volatile uint32_t *)(uintptr_t)UINT32_C(0xF0100000) = status;
    for (;;) { }
}

#endif
