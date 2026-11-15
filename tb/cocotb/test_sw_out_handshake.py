"""Host side of the ``out_handshake`` firmware test (``sw/tests/out_handshake``).

Run with ``python tools/cocotb_runner.py --sw-test out_handshake``. The host
checks each OUT handshake on the bus (ACK, NAK, STALL, no answer to a bad CRC);
the firmware checks the events, the byte counts and the buffer, and starts each
phase with a pull-up toggle. Packet ``n`` carries bytes ``(n << 4) + i``.
"""
import cocotb
from cocotb.triggers import with_timeout

from drivers.usb_fs_bus import CocotbUsbFsPort, UsbFsBus
from drivers.usb_fs_host import UsbFsHost
from models.usb_fs_types import DataPacket, Fault, FaultKind, Pid, TokenPacket
from test_usb_sw import PullUpToggles, wait_for_sw_result
from test_usb_top import start_clocks_and_release_reset

ENDPOINT = 1
DOORBELL = 2
RESPONSE_TIMEOUT_BITS = 18


def _packet(n: int, length: int) -> bytes:
    return bytes(((n << 4) + i) & 0xFF for i in range(length))


async def _out(host: UsbFsHost, payload: bytes, pid: Pid, expected: Pid, endpoint: int = ENDPOINT) -> None:
    result = await host.out(payload, endpoint, pid=pid)
    assert result.pid is expected, f"OUT {pid.name} {payload.hex()}: answered {result.pid}, expected {expected.name}"


@cocotb.test()
async def test_sw_out_handshake(dut) -> None:
    sw_result = cocotb.start_soon(wait_for_sw_result(dut))
    # Returns once the firmware has armed the entry and raised the pull-up.
    await with_timeout(start_clocks_and_release_reset(dut), 5, "ms")
    host = UsbFsHost(UsbFsBus(CocotbUsbFsPort(dut, dp_drive_name="usb_dp_host", dn_drive_name="usb_dn_host")))
    phases = PullUpToggles(dut)

    await _out(host, _packet(0xA, 10), Pid.DATA0, Pid.ACK)
    await _out(host, _packet(0xB, 7), Pid.DATA0, Pid.NAK)

    await phases.next()
    await _out(host, _packet(0xC, 3), Pid.DATA0, Pid.ACK)
    await _out(host, _packet(0xD, 6), Pid.DATA1, Pid.ACK)

    await phases.next()
    await _out(host, _packet(0xE, 5), Pid.DATA0, Pid.STALL)
    await host.send_packet(TokenPacket(Pid.OUT, 0, ENDPOINT))
    await host.send_packet(DataPacket(Pid.DATA0, _packet(0xF, 5)), faults=(Fault(FaultKind.CRC16),))
    response = await host.bus.capture_packet(timeout_cells=64, sop_timeout_cells=RESPONSE_TIMEOUT_BITS)
    assert response is None, f"bad CRC: the device answered {response}"
    await _out(host, b"\x00", Pid.DATA0, Pid.ACK, endpoint=DOORBELL)

    await phases.next()
    await _out(host, _packet(0x6, 4), Pid.DATA0, Pid.ACK)

    await phases.next()
    await _out(host, _packet(0x8, 3), Pid.DATA0, Pid.ACK)

    await sw_result
