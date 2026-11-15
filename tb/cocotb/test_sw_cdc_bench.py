"""Host side of the throughput benchmark firmware (``sw/app/cdc_bench``).

Run with ``python tools/cocotb_runner.py --sw-test cdc_bench``. The board runs the
same firmware against ``tools/usb_bench.py``; this test keeps its command protocol
and data paths working in simulation: ``info``, ``sink`` with the device checking
every byte, ``source`` and ``echo`` checked here, the measurement replies
(``stats``, ``profile``, ``ubench``) and the polling mode (``irq 0``). Lengths are
not multiples of 4 or 64, so the ring copies run at every alignment and wrap.
"""
import cocotb
from cocotb.triggers import Timer
from cocotb.utils import get_sim_time

from drivers.usb_fs_bus import CocotbUsbFsPort, UsbFsBus
from drivers.usb_fs_control import UsbFsControl, setup_packet
from drivers.usb_fs_host import UsbFsHost
from models.usb_fs_types import Pid
from test_sw_cdc_echo import EP_IN, EP_OUT, RESET_FS, SET_ADDRESS, SET_CONFIGURATION, SET_CONTROL_LINE_STATE, bulk_in, bulk_out
from test_usb_top import start_clocks_and_release_reset

ADDRESS = 7


def pattern(position: int, length: int) -> bytes:
    return bytes(((i + (i >> 8)) & 0xFF) for i in range(position, position + length))


class Port:
    def __init__(self, host: UsbFsHost) -> None:
        self.host = host
        self.out_pid = Pid.DATA0
        self.in_pid = Pid.DATA0
        self.pending = b""

    async def send(self, data: bytes) -> None:
        self.out_pid = await bulk_out(self.host, data, self.out_pid)

    async def receive(self, length: int) -> bytes:
        while len(self.pending) < length:
            data, self.in_pid = await bulk_in(self.host, length - len(self.pending), self.in_pid)
            assert data, f"no data from the device, waiting for {length - len(self.pending)} bytes"
            self.pending += data
        data, self.pending = self.pending[:length], self.pending[length:]
        return data

    async def line(self) -> list[str]:
        text = b""
        while not text.endswith(b"\n"):
            text += await self.receive(1)
        return text.decode().split()

    async def command(self, text: str) -> list[str]:
        await self.send(text.encode() + b"\n")
        return await self.line()


@cocotb.test()
async def test_sw_cdc_bench(dut) -> None:
    await start_clocks_and_release_reset(dut, vbus=True)
    host = UsbFsHost(UsbFsBus(CocotbUsbFsPort(dut, dp_drive_name="usb_dp_host", dn_drive_name="usb_dn_host")))
    control = UsbFsControl(host)
    await Timer(300, unit="us")
    await host.bus_reset(RESET_FS)
    assert await control.write(setup_packet(0x00, SET_ADDRESS, ADDRESS)), "SET_ADDRESS"
    host.address = ADDRESS
    await Timer(50, unit="us")
    assert await control.write(setup_packet(0x00, SET_CONFIGURATION, 1)), "SET_CONFIGURATION"
    assert await control.write(setup_packet(0x21, SET_CONTROL_LINE_STATE, 0x0003, 0)), "DTR"
    port = Port(host)

    info = await port.command("info")
    assert info[0] == "INFO" and len(info) == 5, f"info: {info}"

    for size in (1, 67, 301):
        await port.send(f"sink {size} 1\n".encode())
        await port.send(pattern(0, size))
        reply = await port.line()
        assert reply[:3] == ["OK", "sink", str(size)] and reply[5] == "0", f"sink {size}: {reply}"

    await port.send(b"sink 64 1\n")
    await port.send(bytes(64))
    reply = await port.line()
    assert reply[1] == "sink" and int(reply[5]) > 0, f"sink with wrong data must count errors: {reply}"

    for size in (5, 130, 257):
        await port.send(f"source {size}\n".encode())
        data = await port.receive(size)
        assert data == pattern(0, size), f"source {size}: {data.hex()}"
        reply = await port.line()
        assert reply[:3] == ["OK", "source", str(size)], f"source {size}: {reply}"

    for size in (3, 64, 199):
        await port.send(f"echo {size}\n".encode())
        message = pattern(size, size)
        await port.send(message)
        data = await port.receive(size)
        assert data == message, f"echo {size}: {data.hex()}"
        reply = await port.line()
        assert reply[:3] == ["OK", "echo", str(size)], f"echo {size}: {reply}"

    stats = await port.command("stats")
    assert stats[0] == "STATS" and len(stats) == 11, f"stats: {stats}"
    profile = await port.command("profile")
    assert profile[0] == "PROFILE" and len(profile) == 8, f"profile: {profile}"
    ubench = await port.command("ubench")
    assert ubench[0] == "UBENCH" and len(ubench) == 10, f"ubench: {ubench}"

    assert await port.command("irq 0") == ["IRQ", "0"], "polling mode"
    await port.send(b"echo 70\n")
    await port.send(pattern(1, 70))
    assert await port.receive(70) == pattern(1, 70), "echo while polling"
    assert (await port.line())[:2] == ["OK", "echo"], "echo reply while polling"
    assert await port.command("irq 1") == ["IRQ", "1"], "interrupt mode"
    assert await port.command("bogus") == ["ERR"], "unknown command"


STREAM_BYTES = 4096
STREAM_PACKET_US = 53


async def _enumerate(dut) -> Port:
    await start_clocks_and_release_reset(dut, vbus=True)
    host = UsbFsHost(UsbFsBus(CocotbUsbFsPort(dut, dp_drive_name="usb_dp_host", dn_drive_name="usb_dn_host")))
    control = UsbFsControl(host)
    await Timer(300, unit="us")
    await host.bus_reset(RESET_FS)
    assert await control.write(setup_packet(0x00, SET_ADDRESS, ADDRESS)), "SET_ADDRESS"
    host.address = ADDRESS
    await Timer(50, unit="us")
    assert await control.write(setup_packet(0x00, SET_CONFIGURATION, 1)), "SET_CONFIGURATION"
    assert await control.write(setup_packet(0x21, SET_CONTROL_LINE_STATE, 0x0003, 0)), "DTR"
    return Port(host)


@cocotb.test()
async def test_sw_cdc_bench_stream(dut) -> None:
    """Back-to-back packets with no host backoff: a NAK is retried at once and counted.
    With double buffering, the lean trap entry and split IN transfers the device keeps
    up with the bus in both directions: no NAK, and a packet every STREAM_PACKET_US
    (about 51 us of bus time for a 64-byte transaction). The device does not check
    the sink data here; that costs more than the bus leaves."""
    port = await _enumerate(dut)
    host = port.host

    await port.send(f"sink {STREAM_BYTES} 0\n".encode())
    data = pattern(0, STREAM_BYTES)
    naks, start = 0, get_sim_time("us")
    for offset in range(0, STREAM_BYTES, 64):
        while True:
            result = await host.out(data[offset:offset + 64], EP_OUT, pid=port.out_pid)
            if result.pid is not Pid.NAK:
                break
            naks += 1
        assert result.pid is Pid.ACK, f"sink stream: {result}"
        port.out_pid = Pid.DATA1 if port.out_pid is Pid.DATA0 else Pid.DATA0
    elapsed = get_sim_time("us") - start
    reply = await port.line()
    assert reply[:3] == ["OK", "sink", str(STREAM_BYTES)] and reply[5] == "0", f"sink: {reply}"
    dut._log.info("sink stream: %d packets, %d NAKs, %.1f us per packet", STREAM_BYTES // 64, naks,
                  elapsed / (STREAM_BYTES // 64))
    assert naks == 0 and elapsed / (STREAM_BYTES // 64) < STREAM_PACKET_US, \
        f"sink did not keep up: {naks} NAKs, {elapsed / (STREAM_BYTES // 64):.1f} us per packet"

    await port.send(f"source {STREAM_BYTES}\n".encode())
    received, naks, start = b"", 0, None
    while len(received) < STREAM_BYTES:
        result = await host.in_transfer(EP_IN, pid=port.in_pid)
        if result.pid is Pid.NAK:
            if start is not None:
                naks += 1
            continue
        assert result.success, f"source stream: {result}"
        start = start if start is not None else get_sim_time("us")
        received += result.payload
        port.in_pid = Pid.DATA1 if port.in_pid is Pid.DATA0 else Pid.DATA0
    elapsed = get_sim_time("us") - start
    assert received == pattern(0, STREAM_BYTES), "source stream data"
    reply = await port.line()
    assert reply[:3] == ["OK", "source", str(STREAM_BYTES)], f"source: {reply}"
    dut._log.info("source stream: %d packets, %d NAKs, %.1f us per packet", STREAM_BYTES // 64, naks,
                  elapsed / (STREAM_BYTES // 64 - 1))
    assert naks == 0 and elapsed / (STREAM_BYTES // 64 - 1) < STREAM_PACKET_US, \
        f"source did not keep up: {naks} NAKs, {elapsed / (STREAM_BYTES // 64 - 1):.1f} us per packet"
