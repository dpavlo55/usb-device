"""Host side of the ``in_split`` firmware test (``sw/tests/in_split``).

Run with ``python tools/cocotb_runner.py --sw-test in_split``. IN transfers that
hardware splits into packets (``control.split``): the host checks each packet's
data and PID, that the transfer goes on after each ACK, that a transfer of whole
packets ends without a zero-length packet, and that a double-buffered endpoint sends
both buffers' packets in order. The firmware checks one completion per transfer.
Byte ``i`` of entry ``k``'s buffer is ``(k << 4) + i``.
"""
import cocotb
from cocotb.triggers import with_timeout

from drivers.usb_fs_bus import CocotbUsbFsPort, UsbFsBus
from drivers.usb_fs_host import UsbFsHost
from models.usb_fs_types import Pid
from test_usb_sw import PullUpToggles, wait_for_sw_result
from test_usb_top import start_clocks_and_release_reset


def _buffer(entry: int) -> bytes:
    return bytes(((entry << 4) + i) & 0xFF for i in range(128))


async def _in(host: UsbFsHost, endpoint: int, pid: Pid, payload: bytes) -> None:
    result = await host.in_transfer(endpoint, pid=pid)
    assert result.success and result.payload == payload, \
        f"IN {endpoint} {pid.name}: {result}, expected {payload.hex()}"


async def _nak(host: UsbFsHost, endpoint: int) -> None:
    result = await host.in_transfer(endpoint)
    assert result.pid is Pid.NAK, f"IN {endpoint}: {result}, expected NAK"


@cocotb.test()
async def test_sw_in_split(dut) -> None:
    sw_result = cocotb.start_soon(wait_for_sw_result(dut))
    await with_timeout(start_clocks_and_release_reset(dut), 5, "ms")
    host = UsbFsHost(UsbFsBus(CocotbUsbFsPort(dut, dp_drive_name="usb_dp_host", dn_drive_name="usb_dn_host")))
    phases = PullUpToggles(dut)

    a = _buffer(2)
    await _in(host, 1, Pid.DATA0, a[:64])
    result = await host.in_transfer(1, pid=Pid.DATA1)
    assert result.success, f"IN 1: {result}"
    assert result.payload == a[64:100], f"second packet of 100 bytes: {result.payload.hex()}"

    await phases.next()
    await _in(host, 1, Pid.DATA0, a[:64])
    await _in(host, 1, Pid.DATA1, a[64:128])
    await _nak(host, 1)

    await phases.next()
    b, c = _buffer(4), _buffer(5)
    await _in(host, 2, Pid.DATA0, b[:64])
    await _in(host, 2, Pid.DATA1, b[64:])
    await _in(host, 2, Pid.DATA0, c[:64])
    await _in(host, 2, Pid.DATA1, c[64:])
    await _nak(host, 2)

    await sw_result
