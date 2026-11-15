"""Host side of the ``vbus`` firmware test (``sw/tests/vbus``).

Run with ``python tools/cocotb_runner.py --sw-test vbus``. The host plugs and
unplugs VBUS. Hardware gates the D+ pull-up with VBUS (USB 2.0 section 7.1.5.1):
with ``control.enable_pu`` set it rises and falls with VBUS within a few clock
cycles, without the firmware. Every VBUS change sets ``bus_event.vbus``; the host
counts the ``irq`` rises while it is enabled, the firmware checks the registers,
``mip.MEIP`` and the HAL's ``USB_HAL_EVENT_VBUS``.
"""
import cocotb
from cocotb.triggers import FallingEdge, RisingEdge, Timer, with_timeout

from test_usb_sw import wait_for_sw_result
from test_usb_top import start_clocks_and_release_reset

BOOT_US = 1000
PULL_UP_RESPONSE_NS = 200
FIRMWARE_US = 200


@cocotb.test()
async def test_sw_vbus(dut) -> None:
    sw_result = cocotb.start_soon(wait_for_sw_result(dut, timeout_ms=10))
    await start_clocks_and_release_reset(dut, vbus=False)
    rises = 0

    async def count() -> None:
        nonlocal rises
        while True:
            await RisingEdge(dut.usb_irq)
            rises += 1

    cocotb.start_soon(count())

    async def plug(level: int) -> None:
        dut.usb_vbus.value = level
        edge = RisingEdge(dut.usb_pu) if level else FallingEdge(dut.usb_pu)
        await with_timeout(edge, PULL_UP_RESPONSE_NS, "ns")
        await Timer(FIRMWARE_US, unit="us")

    # The firmware sets enable_pu right after booting; without VBUS D+ must stay released.
    await Timer(BOOT_US, unit="us")
    assert int(dut.usb_pu.value) == 0, "pull-up raised without VBUS"
    assert int(dut.usb_irq.value) == 0, "irq high before any VBUS change"

    await plug(1)
    assert rises == 1, f"irq rose {rises} time(s) for VBUS appearing"
    assert int(dut.usb_irq.value) == 0, "irq stayed high after the firmware cleared bus_event"
    await plug(0)
    assert rises == 2, f"irq rose {rises} time(s) after VBUS went away"
    await plug(1)
    assert rises == 2, "irq rose although bus_event.vbus was masked"

    # The HAL phase: one unplug and one replug, each a USB_HAL_EVENT_VBUS.
    await plug(0)
    await plug(1)
    await sw_result
