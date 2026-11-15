#include <stdint.h>
#include <neorv32.h>
#include "platform.h"
#include "../common/test_status.h"

int main(void) {

    neorv32_rte_setup();
    neorv32_uart0_setup(19200, 0);

    while (USB_DEVICE->status.f.vbus_present == 0) { }
    neorv32_uart0_printf("USB VBUS detected!\n");

    USB_DEVICE->control.f.enable_pu = 1;
    neorv32_uart0_printf("Device pull-up enabled!\n");

    test_finish(1);
    return 0;
}
