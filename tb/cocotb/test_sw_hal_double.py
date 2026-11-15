"""Host side of the ``hal_double`` firmware test (``sw/tests/hal_double``).

Run with ``python tools/cocotb_runner.py --sw-test hal_double``. Double-buffered
endpoints in the HAL: with two packets armed the host's packets are ACKed back to
back, and the firmware gets them in arrival order from the right buffers, also when
the second buffer by entry number fills first. Transfers still work one packet at
a time. Packet ``n`` carries bytes ``(n << 4) + i``.
"""
import cocotb
from cocotb.triggers import Timer, with_timeout

from drivers.usb_fs_bus import CocotbUsbFsPort, UsbFsBus
from drivers.usb_fs_host import UsbFsHost
from models.usb_fs_types import Pid
from test_usb_sw import PullUpToggles, wait_for_sw_result
from test_usb_top import start_clocks_and_release_reset

EP = 1
RETRIES = 400


def _packet(n: int, length: int) -> bytes:
    return bytes(((n << 4) + i) & 0xFF for i in range(length))


async def _out_now(host: UsbFsHost, n: int, length: int, pid: Pid) -> None:
    result = await host.out(_packet(n, length), EP, pid=pid)
    assert result.pid is Pid.ACK, f"OUT {pid.name} packet {n:#x}: answered {result.pid}, expected ACK at once"


async def _in_now(host: UsbFsHost, pid: Pid, payload: bytes) -> None:
    result = await host.in_transfer(EP, pid=pid)
    assert result.success and result.payload == payload, f"IN {pid.name}: {result}, expected data at once"


async def _retry(operation):
    for _ in range(RETRIES):
        result = await operation()
        if result.pid is not Pid.NAK:
            return result
        await Timer(2, unit="us")
    raise AssertionError(f"NAKed {RETRIES} times")


@cocotb.test()
async def test_sw_hal_double(dut) -> None:
    sw_result = cocotb.start_soon(wait_for_sw_result(dut, timeout_ms=20))
    await with_timeout(start_clocks_and_release_reset(dut), 5, "ms")
    host = UsbFsHost(UsbFsBus(CocotbUsbFsPort(dut, dp_drive_name="usb_dp_host", dn_drive_name="usb_dn_host")))
    phases = PullUpToggles(dut)

    await _out_now(host, 0xA, 10, Pid.DATA0)
    await _out_now(host, 0xB, 11, Pid.DATA1)

    await phases.next()
    await _out_now(host, 0xC, 12, Pid.DATA0)
    await phases.next()
    await _out_now(host, 0xD, 13, Pid.DATA1)
    await _out_now(host, 0xE, 14, Pid.DATA0)

    await phases.next()
    await _in_now(host, Pid.DATA0, _packet(0x1, 5))
    await _in_now(host, Pid.DATA1, _packet(0x2, 7))

    await phases.next()
    data, pid = _packet(0x3, 150), Pid.DATA1
    for offset in range(0, 150, 64):
        result = await _retry(lambda: host.out(data[offset:offset + 64], EP, pid=pid))
        assert result.pid is Pid.ACK, f"OUT transfer at {offset}: {result}"
        pid = Pid.DATA1 if pid is Pid.DATA0 else Pid.DATA0
    data, pid = _packet(0x4, 150), Pid.DATA0
    for offset in range(0, 150, 64):
        result = await _retry(lambda: host.in_transfer(EP, pid=pid))
        assert result.success and result.payload == data[offset:offset + 64], f"IN transfer at {offset}: {result}"
        pid = Pid.DATA1 if pid is Pid.DATA0 else Pid.DATA0

    await phases.next()
    pid = Pid.DATA0
    for n, length in ((0x5, 200), (0x6, 128)):
        data = _packet(n, length)
        for offset in range(0, length, 64):
            result = await host.in_transfer(2, pid=pid)
            assert result.success and result.payload == data[offset:offset + 64], \
                f"split IN {n:#x} at {offset}: {result}, expected at once"
            pid = Pid.DATA1 if pid is Pid.DATA0 else Pid.DATA0
    result = await host.in_transfer(2)
    assert result.pid is Pid.NAK, f"no zero-length packet after 128 bytes: {result}"

    await phases.next()
    data = _packet(0x7, 192)
    for offset in range(0, 256, 64):
        result = await _retry(lambda: host.in_transfer(2, pid=pid))
        assert result.success and result.payload == data[offset:offset + 64], f"transfer with ZLP at {offset}: {result}"
        pid = Pid.DATA1 if pid is Pid.DATA0 else Pid.DATA0

    await sw_result
