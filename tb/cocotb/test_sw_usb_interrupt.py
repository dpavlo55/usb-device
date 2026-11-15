"""Host side of the ``usb_interrupt`` firmware test (``sw/tests/usb_interrupt``).

Run with ``python tools/cocotb_runner.py --sw-test usb_interrupt``. The host
watches usb_top's ``irq`` (also the CPU's machine external interrupt): it must
stay low while ``interrupt_enable`` masks a pending bus event, rise for an
enabled bus reset and an enabled SETUP, and fall once the firmware clears the
cause. The firmware checks the CPU side (``mip.MEIP``) and the registers.
"""
import cocotb
from cocotb.triggers import RisingEdge, with_timeout

from drivers.usb_fs_bus import CocotbUsbFsPort, UsbFsBus
from drivers.usb_fs_host import UsbFsHost
from test_usb_sw import PullUpToggles, wait_for_sw_result
from test_usb_top import start_clocks_and_release_reset

BUS_RESET_FS = 10_000_000_000
SETUP = bytes((0x80, 0x06, 0x00, 0x01, 0x00, 0x00, 0x12, 0x00))


@cocotb.test()
async def test_sw_usb_interrupt(dut) -> None:
    sw_result = cocotb.start_soon(wait_for_sw_result(dut))
    rises = 0

    async def count() -> None:
        nonlocal rises
        while True:
            await RisingEdge(dut.usb_irq)
            rises += 1

    # Returns once the firmware has configured the core and raised the pull-up.
    await with_timeout(start_clocks_and_release_reset(dut), 5, "ms")
    cocotb.start_soon(count())
    host = UsbFsHost(UsbFsBus(CocotbUsbFsPort(dut, dp_drive_name="usb_dp_host", dn_drive_name="usb_dn_host")))
    phases = PullUpToggles(dut)
    assert int(dut.usb_irq.value) == 0, "irq is high after reset"

    await host.bus_reset(BUS_RESET_FS)
    await phases.next()
    assert rises == 0, f"irq rose {rises} time(s) while interrupt_enable masked the bus event"

    await host.bus_reset(BUS_RESET_FS)
    await phases.next()
    assert rises == 1, f"irq rose {rises} time(s) for one enabled bus reset"
    assert int(dut.usb_irq.value) == 0, "irq stayed high after the firmware cleared bus_event"

    result = await host.setup(SETUP)
    assert result.success, f"SETUP was not ACKed: {result}"
    await sw_result
    assert rises == 2, f"irq rose {rises} time(s) in total, expected 2"
    assert int(dut.usb_irq.value) == 0, "irq stayed high after the firmware cleared the SETUP interrupt"
