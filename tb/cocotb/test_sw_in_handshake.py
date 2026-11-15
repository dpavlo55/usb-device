"""Host side of the ``in_handshake`` firmware test (``sw/tests/in_handshake``).

Run with ``python tools/cocotb_runner.py --sw-test in_handshake``. The host
checks each IN answer on the bus (data and PID, NAK, STALL) and decides whether
to ACK; the firmware checks the events and the arm state, and starts each phase
with a pull-up toggle. Packet ``n`` carries bytes ``(n << 4) + i``.
"""
import cocotb
from cocotb.triggers import with_timeout

from drivers.usb_fs_bus import CocotbUsbFsPort, UsbFsBus
from drivers.usb_fs_host import UsbFsHost
from models.usb_fs_timing import FS_BIT_PERIOD_FS
from models.usb_fs_types import DataPacket, HandshakePacket, Pid, TokenPacket
from test_usb_sw import PullUpToggles, wait_for_sw_result
from test_usb_top import start_clocks_and_release_reset

ENDPOINT = 1
DOORBELL = 2
RESPONSE_TIMEOUT_BITS = 18


def _packet(n: int, length: int) -> bytes:
    return bytes(((n << 4) + i) & 0xFF for i in range(length))


async def _in(host: UsbFsHost, pid: Pid, payload: bytes) -> None:
    """IN with the host's ACK; the device must send ``payload`` with ``pid``."""
    result = await host.in_transfer(ENDPOINT, pid=pid)
    assert result.success, f"IN expecting {pid.name}: {result}"
    assert result.payload == payload, f"IN {pid.name}: payload {result.payload.hex()}, expected {payload.hex()}"


async def _in_answer(host: UsbFsHost):
    """IN without the host's ACK; returns the device's answer."""
    await host.send_packet(TokenPacket(Pid.IN, 0, ENDPOINT))
    capture = await host.bus.capture_packet(timeout_cells=1024, sop_timeout_cells=RESPONSE_TIMEOUT_BITS)
    assert capture is not None and capture.ok, f"IN: no valid answer ({capture})"
    await host.bus.port.wait_fs(30 * FS_BIT_PERIOD_FS)
    return capture.packet


async def _doorbell(host: UsbFsHost, pid: Pid) -> None:
    result = await host.out(b"\x00", DOORBELL, pid=pid)
    assert result.pid is Pid.ACK, f"doorbell: answered {result.pid}"


@cocotb.test()
async def test_sw_in_handshake(dut) -> None:
    sw_result = cocotb.start_soon(wait_for_sw_result(dut))
    # Returns once the firmware has armed P1 and raised the pull-up.
    await with_timeout(start_clocks_and_release_reset(dut), 5, "ms")
    host = UsbFsHost(UsbFsBus(CocotbUsbFsPort(dut, dp_drive_name="usb_dp_host", dn_drive_name="usb_dn_host")))
    phases = PullUpToggles(dut)

    await _in(host, Pid.DATA0, _packet(0x1, 8))
    answer = await _in_answer(host)
    assert answer == HandshakePacket(Pid.NAK), f"unarmed IN: answered {answer}, expected NAK"
    await _doorbell(host, Pid.DATA0)

    await phases.next()
    answer = await _in_answer(host)
    assert answer == DataPacket(Pid.DATA1, _packet(0x2, 5)), f"first IN of P2: {answer}"
    await _in(host, Pid.DATA1, _packet(0x2, 5))

    await phases.next()
    await _in(host, Pid.DATA0, b"")

    await phases.next()
    answer = await _in_answer(host)
    assert answer == HandshakePacket(Pid.STALL), f"stalled IN: answered {answer}, expected STALL"
    await _doorbell(host, Pid.DATA1)

    await phases.next()
    await _in(host, Pid.DATA0, _packet(0x4, 4))

    await sw_result
