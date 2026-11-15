"""Boot project firmware and allow it to print register values via UART0."""
import cocotb
from cocotb.triggers import Timer

from test_usb_top import start_clocks_and_release_reset


@cocotb.test()
async def test_usb_firmware(dut):
    await start_clocks_and_release_reset(dut, wait_for_pullup=False)

    # Give the firmware time to read and print. Register values and output text
    # are not compared against expectations.
    await Timer(1, unit="ms")
