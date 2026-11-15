"""Host side of the ``usb_hal`` firmware test (``sw/tests/usb_hal``).

Run with ``python tools/cocotb_runner.py --sw-test usb_hal``. Layer 2 of the USB
device library: transfers split into and gathered from packets, ZLP, stall, SETUP
events, the zero-copy path and bus reset. The host retries on NAK until the
firmware is ready; packet ``n`` carries bytes ``(n << 4) + i``.
"""
import cocotb
from cocotb.triggers import Timer, with_timeout

from drivers.usb_fs_bus import CocotbUsbFsPort, UsbFsBus
from drivers.usb_fs_host import UsbFsHost
from models.usb_fs_types import Pid
from test_usb_sw import wait_for_sw_result
from test_usb_top import start_clocks_and_release_reset

EP = 1
RETRIES = 400


def _packet(n: int, length: int) -> bytes:
    return bytes(((n << 4) + i) & 0xFF for i in range(length))


async def _out(host: UsbFsHost, payload: bytes, pid: Pid, endpoint: int = EP) -> None:
    for _ in range(RETRIES):
        result = await host.out(payload, endpoint, pid=pid)
        if result.pid is not Pid.NAK:
            assert result.pid is Pid.ACK, f"OUT {pid.name} {payload.hex()}: answered {result.pid}"
            return
        await Timer(2, unit="us")
    raise AssertionError(f"OUT {pid.name} {payload.hex()}: NAKed {RETRIES} times")


async def _in(host: UsbFsHost, pid: Pid, endpoint: int = EP):
    for _ in range(RETRIES):
        result = await host.in_transfer(endpoint, pid=pid)
        if result.pid is not Pid.NAK:
            return result
        await Timer(2, unit="us")
    raise AssertionError(f"IN expecting {pid.name}: NAKed {RETRIES} times")


async def _in_data(host: UsbFsHost, pid: Pid, payload: bytes) -> None:
    result = await _in(host, pid)
    assert result.success and result.payload == payload, f"IN {pid.name}: {result}"


async def _out_transfer(host: UsbFsHost, n: int, length: int, first: Pid) -> Pid:
    data, pid = _packet(n, length), first
    for offset in range(0, max(length, 1), 64):
        await _out(host, data[offset:offset + 64], pid)
        pid = Pid.DATA1 if pid is Pid.DATA0 else Pid.DATA0
    return pid


@cocotb.test()
async def test_sw_usb_hal(dut) -> None:
    sw_result = cocotb.start_soon(wait_for_sw_result(dut, timeout_ms=20))
    await with_timeout(start_clocks_and_release_reset(dut), 5, "ms")
    host = UsbFsHost(UsbFsBus(CocotbUsbFsPort(dut, dp_drive_name="usb_dp_host", dn_drive_name="usb_dn_host")))

    out_pid = await _out_transfer(host, 0x1, 150, Pid.DATA0)              # 64 + 64 + 22
    for pid, chunk in ((Pid.DATA0, 0), (Pid.DATA1, 64), (Pid.DATA0, 128)):  # 150 split by the HAL
        await _in_data(host, pid, _packet(0x2, 150)[chunk:chunk + 64])
    for pid, chunk in ((Pid.DATA1, 0), (Pid.DATA0, 64)):                   # 128 and a closing ZLP
        await _in_data(host, pid, _packet(0x3, 128)[chunk:chunk + 64])
    await _in_data(host, Pid.DATA1, b"")
    out_pid = await _out_transfer(host, 0x4, 64, out_pid)                 # exactly the length asked
    out_pid = await _out_transfer(host, 0x5, 10, out_pid)                 # short packet ends it

    result = await _in(host, Pid.DATA0)
    assert result.pid is Pid.STALL, f"stalled IN: {result}"
    await _out(host, _packet(0x6, 1), out_pid)                            # tells the firmware to resume
    for _ in range(RETRIES):                                              # STALL until it has resumed
        result = await _in(host, Pid.DATA0)
        if result.pid is not Pid.STALL:
            break
        await Timer(2, unit="us")
    assert result.success and result.payload == _packet(0x7, 4), f"IN after resume: {result}"  # toggle back at DATA0

    result = await host.setup(bytes((0x80, 0x06, 0x00, 0x01, 0x00, 0x00, 0x12, 0x00)))
    assert result.success, f"SETUP 1: {result}"
    for _ in range(RETRIES):
        result = await host.in_transfer(0, pid=Pid.DATA1)
        if result.pid is not Pid.NAK:
            break
        await Timer(2, unit="us")
    assert result.pid is Pid.STALL, f"EP0 after a request error: {result}"
    result = await host.setup(bytes((0x80, 0x00, 0x00, 0x00, 0x00, 0x00, 0x02, 0x00)))
    assert result.success, f"SETUP 2: {result}"
    result = await _in(host, Pid.DATA1, endpoint=0)
    assert result.success and result.payload == b"\x01\x00", f"control read: {result}"
    await _out(host, b"", Pid.DATA1, endpoint=0)                          # status stage

    await _in_data(host, Pid.DATA1, bytes(range(8)))                      # zero-copy buffer
    await host.bus_reset(10_000_000_000)

    await sw_result
