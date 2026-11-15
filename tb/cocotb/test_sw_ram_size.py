"""Host side of the ``ram_size`` firmware test (``sw/tests/ram_size``).

Run with ``python tools/cocotb_runner.py --sw-test ram_size``. The firmware checks
the whole RAM, whatever size rdl/usb_device.rdl sets, from the CPU side; the host sends an OUT packet into a buffer at the top of the RAM and
reads an IN packet from just below it.
"""
import cocotb
from cocotb.triggers import with_timeout

from drivers.usb_fs_bus import CocotbUsbFsPort, UsbFsBus
from drivers.usb_fs_host import UsbFsHost
from models.usb_fs_types import Pid
from test_usb_sw import wait_for_sw_result
from test_usb_top import start_clocks_and_release_reset

IN_DATA = bytes(range(64))
# The firmware checks every word before it attaches; 64 KiB takes about 35 ms.
TIMEOUT_MS = 80
OUT_DATA = bytes(b ^ 0xFF for b in IN_DATA)


@cocotb.test()
async def test_sw_ram_size(dut) -> None:
    sw_result = cocotb.start_soon(wait_for_sw_result(dut, timeout_ms=TIMEOUT_MS))
    # Returns once the firmware has checked the RAM, armed both entries and raised the pull-up.
    await with_timeout(start_clocks_and_release_reset(dut), TIMEOUT_MS, "ms")
    host = UsbFsHost(UsbFsBus(CocotbUsbFsPort(dut, dp_drive_name="usb_dp_host", dn_drive_name="usb_dn_host")))

    result = await host.out(OUT_DATA, 1, pid=Pid.DATA0)
    assert result.pid is Pid.ACK, f"OUT into the top of the RAM: answered {result.pid}"
    result = await host.in_transfer(1, pid=Pid.DATA0)
    assert result.success and result.payload == IN_DATA, f"IN from below the top of the RAM: {result}"

    await sw_result
