"""Host side of the ``setup`` firmware test (``sw/tests/setup``).

Run with ``python tools/cocotb_runner.py --sw-test setup``. The host checks the
device's answers on the bus: a good SETUP is ACKed (even while EP0 is stalled),
and SETUP data with a bad CRC, a DATA1 PID or a wrong length (7 or 12 bytes) gets
no answer. A 1-byte OUT to endpoint 1 then tells the firmware to check that none
of the rejected SETUPs reached the buffer (SETUP data is written only after it
is accepted, USB 2.0 section 8.5.3). The firmware checks the registers and the
SETUP bytes in the buffer, and that a SETUP writes no more than 8 bytes.
"""
import cocotb
from cocotb.triggers import with_timeout

from drivers.usb_fs_bus import CocotbUsbFsPort, UsbFsBus
from drivers.usb_fs_host import UsbFsHost
from models.usb_fs_types import DataPacket, Fault, FaultKind, Pid, TokenPacket
from test_usb_sw import PullUpToggles, wait_for_sw_result
from test_usb_top import start_clocks_and_release_reset

SETUP_1 = bytes((0x80, 0x06, 0x00, 0x01, 0x00, 0x00, 0x40, 0x00))
SETUP_2 = bytes((0x00, 0x05, 0x07, 0x00, 0x00, 0x00, 0x00, 0x00))
JUNK = bytes(0x5A ^ i for i in range(12))
RESPONSE_TIMEOUT_BITS = 18


async def _expect_no_answer(host: UsbFsHost, data: DataPacket, faults=(), what: str = "") -> None:
    await host.send_packet(TokenPacket(Pid.SETUP, 0, 0))
    await host.send_packet(data, faults=faults)
    response = await host.bus.capture_packet(timeout_cells=64, sop_timeout_cells=RESPONSE_TIMEOUT_BITS)
    assert response is None, f"{what}: the device answered {response}"


@cocotb.test()
async def test_sw_setup(dut) -> None:
    sw_result = cocotb.start_soon(wait_for_sw_result(dut))
    # Returns once the firmware has configured EP0 and raised the pull-up.
    await with_timeout(start_clocks_and_release_reset(dut), 5, "ms")
    host = UsbFsHost(UsbFsBus(CocotbUsbFsPort(dut, dp_drive_name="usb_dp_host", dn_drive_name="usb_dn_host")))
    phases = PullUpToggles(dut)

    result = await host.setup(SETUP_1)
    assert result.success and result.pid is Pid.ACK, f"SETUP_1 was not ACKed: {result}"

    await phases.next()
    await _expect_no_answer(host, DataPacket(Pid.DATA0, JUNK[:8]), (Fault(FaultKind.CRC16),), "bad CRC")
    await _expect_no_answer(host, DataPacket(Pid.DATA1, JUNK[:8]), what="DATA1")
    await _expect_no_answer(host, DataPacket(Pid.DATA0, JUNK[:7]), what="7 bytes")
    await _expect_no_answer(host, DataPacket(Pid.DATA0, JUNK), what="12 bytes")
    result = await host.out(b"", 1, pid=Pid.DATA0)
    assert result.pid is Pid.ACK, f"doorbell OUT to endpoint 1: answered {result.pid}"

    await phases.next()
    result = await host.setup(SETUP_2)
    assert result.success and result.pid is Pid.ACK, f"SETUP_2 was not ACKed: {result}"

    await sw_result
