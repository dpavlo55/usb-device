#include <neorv32.h>
#include "platform.h"

int main(void) {
    neorv32_rte_setup();
    neorv32_uart0_setup(19200, 0);

    /* Attach the device so USB packet tests can run with this firmware. */
    while (USB_DEVICE->status.f.vbus_present == 0) { }
    USB_DEVICE->control.f.enable_pu = 1;

    const uint32_t core_id = USB_DEVICE->core_id.f.id;
    const usb_device__version_t version = { .w = USB_DEVICE->version.w };

    const uint8_t major = version.f.major;
    const uint8_t minor = version.f.minor;
    const uint8_t patch = version.f.patch;

    neorv32_uart0_printf("USB core ID: 0x%x\n", core_id);
    neorv32_uart0_printf("USB version: 0x%x (%u.%u.%u)\n", version.w, major, minor, patch);

    USB_DEVICE->endpoint[0].control.f.enable = 1;
    USB_DEVICE->endpoint[0].control.f.enable = 0;
    USB_DEVICE->endpoint[0].control.f.enable = 1;

    //volatile uint32_t *const usb_ram =
    //    (volatile uint32_t *)(uintptr_t)USB_RAM_BASE;

    ///* Byte offset 4 selects row 1 in all four byte-wide RAM banks. */
    //usb_ram[1] = UINT32_C(0x12345678);
    //uint32_t ram_value = usb_ram[1];
    //neorv32_uart0_printf("USB RAM word readback: 0x%x (expected 0x12345678)\n", ram_value);
    //if (ram_value != UINT32_C(0x12345678)) {
    //    neorv32_uart0_printf("USB RAM test FAIL: word write/read\n");
    //    return 1;
    //}

    uint8_t ram_value;

    /* Update bank 0 only; the other three bytes must retain their values. */
    USB_DEVICE->usb_ram.mem[4] = 0xABu;
    USB_DEVICE->usb_ram.mem[5] = 0xCDu;
    ram_value = USB_DEVICE->usb_ram.mem[4];
    neorv32_uart0_printf("USB RAM byte readback: 0x%x)\n", ram_value);
    ram_value = USB_DEVICE->usb_ram.mem[5];
    neorv32_uart0_printf("USB RAM byte readback: 0x%x)\n", ram_value);

    return 0;
}
