"""Host side of the ``endpoint_transfer`` firmware test (``sw/tests/endpoint_transfer``).

Run with ``python tools/cocotb_runner.py --sw-test endpoint_transfer``. The
firmware arms EP0 as an OUT endpoint and uses the pull-up as a handshake; every
DATA packet follows an OUT token to address 0, endpoint 0, which routes it, and
the host waits for the device's handshake (ACK when armed, NAK when not). The
firmware itself checks the interrupts, ``status`` and the RAM, and reports
the result through ``sw_test_status``.
"""
import cocotb
from cocotb.triggers import with_timeout

from drivers.usb_fs_bus import CocotbUsbFsPort, UsbFsBus
from drivers.usb_fs_host import UsbFsHost
from models.usb_fs_timing import FS_RESET_DETECT_FS
from models.usb_fs_types import Pid
from test_usb_sw import PullUpToggles, wait_for_sw_result
from test_usb_top import start_clocks_and_release_reset

OUT_A = bytes((7 * i + 3) & 0xFF for i in range(20))
OUT_B = bytes((0xA5 ^ i) for i in range(5))


@cocotb.test()
async def test_sw_endpoint_transfer(dut) -> None:
    sw_result = cocotb.start_soon(wait_for_sw_result(dut))
    await with_timeout(start_clocks_and_release_reset(dut), 5, "ms")
    host = UsbFsHost(UsbFsBus(CocotbUsbFsPort(dut, dp_drive_name="usb_dp_host", dn_drive_name="usb_dn_host")))
    phases = PullUpToggles(dut)
    await host.bus_reset(FS_RESET_DETECT_FS)

    for payload, pid, expected in ((OUT_A, Pid.DATA0, Pid.ACK), (OUT_B, Pid.DATA1, Pid.NAK)):
        result = await host.out(payload, 0, pid=pid)
        assert result.pid is expected, f"OUT {pid.name}: answered {result.pid}, expected {expected.name}"

    await phases.next()
    result = await host.out(b"", 0, pid=Pid.DATA1)
    assert result.pid is Pid.ACK, f"zero-length OUT: answered {result.pid}, expected ACK"

    await sw_result
