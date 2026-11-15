"""Host side of the ``bus_reset`` firmware test (``sw/tests/bus_reset``).

Run with ``python tools/cocotb_runner.py --sw-test bus_reset``. The firmware
writes ``device_address`` and raises the pull-up; the host then drives a bus
reset. The firmware checks ``bus_event.bus_reset`` and that hardware cleared
``device_address``, and reports the result through ``sw_test_status``.
"""
import cocotb
from cocotb.triggers import with_timeout

from drivers.usb_fs_bus import CocotbUsbFsPort, UsbFsBus
from drivers.usb_fs_host import UsbFsHost
from test_usb_sw import wait_for_sw_result
from test_usb_top import start_clocks_and_release_reset

BUS_RESET_FS = 10_000_000_000
"""10 us: well above the 2.5 us detection threshold, short enough to simulate."""


@cocotb.test()
async def test_sw_bus_reset(dut) -> None:
    sw_result = cocotb.start_soon(wait_for_sw_result(dut))
    # Returns once the firmware has raised the pull-up.
    await with_timeout(start_clocks_and_release_reset(dut), 5, "ms")
    host = UsbFsHost(UsbFsBus(CocotbUsbFsPort(dut, dp_drive_name="usb_dp_host", dn_drive_name="usb_dn_host")))
    await host.bus_reset(BUS_RESET_FS)
    await sw_result
