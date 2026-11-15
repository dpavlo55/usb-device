"""Host side of the ``usb_ll_ram`` firmware test (``sw/tests/usb_ll_ram``).

Run with ``python tools/cocotb_runner.py --sw-test usb_ll_ram``. The firmware tests
layer 1 of the USB device library on its own; the host only waits, longer than the
default, because the copy check covers many lengths with every alignment.
"""
import cocotb

from test_usb_sw import wait_for_sw_result
from test_usb_top import start_clocks_and_release_reset


@cocotb.test()
async def test_sw_usb_ll_ram(dut) -> None:
    await start_clocks_and_release_reset(dut, wait_for_pullup=False)
    await wait_for_sw_result(dut, timeout_ms=40)
