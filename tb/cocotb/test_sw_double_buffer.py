"""Host side of the ``double_buffer`` firmware test (``sw/tests/double_buffer``).

Run with ``python tools/cocotb_runner.py --sw-test double_buffer``. Endpoint 1 OUT
and IN each have two buffers (entries 2/3 and 4/5). The host checks that both
buffers take packets back to back, that a packet never skips to the other buffer
(NAK while it is the unarmed buffer's turn) and that IN data alternates between the
two buffers; the firmware checks the events, the buffers and ``status.next_buffer``.
Packet ``n`` carries bytes ``(n << 4) + i``.
"""
import cocotb
from cocotb.triggers import with_timeout

from drivers.usb_fs_bus import CocotbUsbFsPort, UsbFsBus
from drivers.usb_fs_host import UsbFsHost
from models.usb_fs_types import Pid
from test_usb_sw import PullUpToggles, wait_for_sw_result
from test_usb_top import start_clocks_and_release_reset

ENDPOINT = 1


def _packet(n: int, length: int) -> bytes:
    return bytes(((n << 4) + i) & 0xFF for i in range(length))


async def _out(host: UsbFsHost, n: int, length: int, pid: Pid, expected: Pid) -> None:
    result = await host.out(_packet(n, length), ENDPOINT, pid=pid)
    assert result.pid is expected, f"OUT {pid.name} packet {n:#x}: answered {result.pid}, expected {expected.name}"


async def _in(host: UsbFsHost, expected: Pid, payload: bytes = b"") -> None:
    result = await host.in_transfer(ENDPOINT, pid=expected if expected is not Pid.NAK else None)
    assert result.pid is expected, f"IN: answered {result.pid}, expected {expected.name}"
    if expected is not Pid.NAK:
        assert result.payload == payload, f"IN {expected.name}: {result.payload.hex()}, expected {payload.hex()}"


@cocotb.test()
async def test_sw_double_buffer(dut) -> None:
    sw_result = cocotb.start_soon(wait_for_sw_result(dut))
    await with_timeout(start_clocks_and_release_reset(dut), 5, "ms")
    host = UsbFsHost(UsbFsBus(CocotbUsbFsPort(dut, dp_drive_name="usb_dp_host", dn_drive_name="usb_dn_host")))
    phases = PullUpToggles(dut)

    await _out(host, 0xA, 10, Pid.DATA0, Pid.ACK)
    await _out(host, 0xB, 11, Pid.DATA1, Pid.ACK)
    await _out(host, 0xC, 12, Pid.DATA0, Pid.NAK)

    await phases.next()
    await _out(host, 0xC, 12, Pid.DATA0, Pid.NAK)

    await phases.next()
    await _out(host, 0xC, 12, Pid.DATA0, Pid.ACK)
    await _out(host, 0xD, 13, Pid.DATA1, Pid.ACK)

    await phases.next()
    await _out(host, 0xE, 14, Pid.DATA0, Pid.ACK)
    await _out(host, 0xF, 15, Pid.DATA1, Pid.NAK)

    await phases.next()
    await _out(host, 0xF, 15, Pid.DATA1, Pid.ACK)

    await phases.next()
    await _in(host, Pid.DATA0, _packet(0x1, 5))
    await _in(host, Pid.DATA1, _packet(0x2, 7))
    await _in(host, Pid.NAK)

    await sw_result
