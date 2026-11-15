"""Cocotb AXI4-Stream correctness tests for usb_packet_deserializer."""
from __future__ import annotations

import logging
import os
import random
from fractions import Fraction

import cocotb
from cocotb.triggers import (
    FallingEdge,
    ReadOnly,
    RisingEdge,
    SimTimeoutError,
    Timer,
    with_timeout,
)
from cocotbext.axi import AxiStreamBus, AxiStreamSink

from drivers.usb_fs_bus import CocotbUsbFsPort, UsbFsBus
from drivers.usb_fs_host import UsbFsHost
from models.usb_fs_codec import encode_packet_bytes
from models.usb_fs_timing import FS_BIT_PERIOD_FS, FS_RESET_DETECT_FS
from models.usb_fs_types import (
    DataPacket,
    LineState,
    HandshakePacket,
    Packet,
    Pid,
    SofPacket,
    TokenPacket,
)
from drivers.clock_reset import ClockDomain, start_clocks_and_reset

# 48 MHz clock: ~4 clocks per USB FS bit.  A max-size packet (SYNC + 67 bytes
# + stuffing + EOP) is ≈ 650 bit periods ≈ 2600 clocks.  Leave generous margin.
# _POST_EOP_SETTLE_CYCLES accounts for 2-stage CDC + phy_rx pipeline + margin.
_POST_EOP_SETTLE_CYCLES = 64
_RANDOM_PACKET_COUNT_RANGE = (32, 65)


async def _start_clock_and_release_reset(dut) -> None:
    dut.usb_dp_host.value = "Z"
    dut.usb_dn_host.value = "Z"
    dut.tx_active.value = 0
    dut.packet_tready.value = 0
    await start_clocks_and_reset(ClockDomain(dut.clk, dut.rst_n, Fraction(20833, 1000)))
    await Timer(1, unit="ns")


def _make_sink(dut) -> AxiStreamSink:
    sink = AxiStreamSink(
        AxiStreamBus.from_prefix(dut, "packet"),
        dut.clk,
        dut.rst_n,
        reset_active_level=0,
    )
    sink.log.setLevel(logging.WARNING)  # Suppress verbose per-frame dumps.
    cocotb.start_soon(_monitor_axis(dut))
    return sink


def _axis_beat(dut) -> tuple[int, int, int, int]:
    return tuple(int(getattr(dut, f"packet_t{field}").value)
                 for field in ("data", "keep", "dest", "last"))


async def _monitor_axis(dut) -> None:
    """Check sideband semantics and hold the whole valid beat during stalls."""
    stalled: tuple[int, int, int, int] | None = None
    while True:
        await FallingEdge(dut.clk)
        await ReadOnly()
        if not int(dut.rst_n.value):
            stalled = None
            assert int(dut.packet_tvalid.value) == 0, "TVALID did not reset"
            continue
        valid = int(dut.packet_tvalid.value)
        ready = int(dut.packet_tready.value)
        beat = _axis_beat(dut)
        if stalled is not None:
            assert valid and beat == stalled, (
                f"AXI4-Stream beat changed while stalled: previous={stalled}, current={beat}"
            )
        if valid:
            data, keep, dest, last = beat
            assert dest == 0, f"Unexpected TDEST={dest}; deserializer emits destination zero"
            assert keep == int(not last), f"Unexpected TKEEP/TLAST combination: {beat}"
            if last:
                assert data == 0, f"EOP marker must have TDATA=0: {beat}"
        assert not int(dut.stuff_error.value), "Unexpected bit-stuffing error"
        assert not int(dut.sync_error.value), "Unexpected SYNC error"
        stalled = beat if valid and not ready else None


async def _receive_frame(sink: AxiStreamSink, expected: bytes):
    # Do not compact: that would discard the KEEP=0 EOP beat and hide sideband bugs.
    try:
        return await with_timeout(sink.recv(compact=False), len(expected) + 16, "us")
    except SimTimeoutError as exc:
        raise AssertionError(
            f"Timed out receiving {len(expected)} packet bytes and the KEEP=0/LAST=1 "
            f"EOP marker; incomplete frame at sink={sink.active}"
        ) from exc


def _check_frame(packet: Packet, expected: bytes, frame, **ctx) -> None:
    raw_expected = expected + b"\x00"
    assert bytes(frame.tdata) == raw_expected, _failure_msg(
        packet, raw_expected, bytes(frame.tdata), **ctx
    )
    assert frame.tkeep == [1] * len(expected) + [0], (
        f"{ctx}: expected one KEEP=1 beat per byte, then a KEEP=0 EOP beat; "
        f"observed TKEEP={frame.tkeep}"
    )
    assert frame.tdest == [0] * len(raw_expected), f"{ctx}: unexpected TDEST={frame.tdest}"
    # The sink only completes a frame on an accepted TLAST. Its raw length and
    # KEEP pattern therefore also verify that TLAST is on the separate EOP beat.


async def _assert_quiet(dut, sink: AxiStreamSink) -> None:
    for _ in range(_POST_EOP_SETTLE_CYCLES):
        await RisingEdge(dut.clk)
        await ReadOnly()
        assert int(dut.packet_tvalid.value) == 0, "Unexpected trailing stream beat"
    assert sink.empty() and not sink.active, "Unexpected extra or incomplete stream frame"


def _make_host(dut) -> UsbFsHost:
    port = CocotbUsbFsPort(
        dut,
        dp_drive_name="usb_dp_host",
        dn_drive_name="usb_dn_host",
    )
    return UsbFsHost(UsbFsBus(port))


def _seed_from_environment() -> int:
    value = os.environ.get("SEED")
    if value is not None:
        return int(value, 0)
    return random.SystemRandom().getrandbits(32)


def _random_packet(rng: random.Random) -> Packet:
    kind = rng.choice(("data", "token", "sof", "handshake"))
    if kind == "data":
        pid = rng.choice((Pid.DATA0, Pid.DATA1))
        payload = bytes(rng.randrange(0x100) for _ in range(rng.randrange(65)))
        return DataPacket(pid, payload)
    if kind == "token":
        pid = rng.choice((Pid.SETUP, Pid.OUT, Pid.IN))
        return TokenPacket(pid, rng.randrange(128), rng.randrange(16))
    if kind == "sof":
        return SofPacket(rng.randrange(0x800))
    return HandshakePacket(rng.choice((Pid.ACK, Pid.NAK, Pid.STALL)))


def _failure_msg(packet: Packet, expected: bytes, got: bytes, **ctx) -> str:
    parts = [f"kind={type(packet).__name__} pid={packet.pid.name}"]
    parts += [f"{k}={v}" for k, v in ctx.items()]
    parts += [f"expected({len(expected)})={expected.hex()}"]
    parts += [f"got({len(got)})={got.hex()}"]
    return " ".join(parts)


@cocotb.test()
async def test_axis_stream_random(dut) -> None:
    """Verify bytes, KEEP, DEST and the separate LAST/EOP beat for random packets."""
    await _start_clock_and_release_reset(dut)
    sink = _make_sink(dut)
    host = _make_host(dut)
    await host.bus_reset(FS_RESET_DETECT_FS)

    seed = _seed_from_environment()
    rng = random.Random(seed)
    packet_count = rng.randrange(*_RANDOM_PACKET_COUNT_RANGE)
    cocotb.log.info(
        "deserializer AXI stream random seed=%d (0x%X) packet_count=%d",
        seed,
        seed,
        packet_count,
    )

    for case_index in range(packet_count):
        packet = _random_packet(rng)
        expected = bytes(encode_packet_bytes(packet))
        cocotb.log.info(
            "case=%d/%d kind=%s pid=%s expected_bytes=%d",
            case_index + 1,
            packet_count,
            type(packet).__name__,
            packet.pid.name,
            len(expected),
        )

        recv_task = cocotb.start_soon(_receive_frame(sink, expected))
        await host.send_packet(packet)
        frame = await recv_task

        _check_frame(packet, expected, frame, seed=seed, case=case_index)
    await _assert_quiet(dut, sink)


@cocotb.test()
async def test_axis_stream_backpressure(dut) -> None:
    """Stall a one-byte packet through EOP, then receive the byte and EOP marker."""
    await _start_clock_and_release_reset(dut)
    sink = _make_sink(dut)
    host = _make_host(dut)
    await host.bus_reset(FS_RESET_DETECT_FS)

    # A USB handshake produces two AXI beats: the PID (KEEP=1, LAST=0), then
    # the EOP marker (KEEP=0, LAST=1). The PID must remain visible until ready.
    packet = HandshakePacket(Pid.ACK)
    expected = bytes(encode_packet_bytes(packet))

    sink.pause = True
    recv_task = cocotb.start_soon(_receive_frame(sink, expected))
    await host.send_packet(packet)

    # Give the DUT time to process EOP and assert tvalid.
    for _ in range(_POST_EOP_SETTLE_CYCLES):
        await RisingEdge(dut.clk)
    await ReadOnly()

    assert int(dut.packet_tready.value) == 0
    assert int(dut.packet_tvalid.value) == 1, "TVALID must remain high while stalled"
    assert _axis_beat(dut) == (expected[0], 1, 0, 0), "Stalled PID beat was corrupted"
    for _ in range(8):
        await RisingEdge(dut.clk)
        await ReadOnly()
        assert int(dut.packet_tvalid.value) == 1
        assert _axis_beat(dut) == (expected[0], 1, 0, 0)
    assert not recv_task.done(), "sink must not complete a transfer while paused"

    await Timer(1, unit="ps")
    sink.pause = False
    frame = await recv_task

    _check_frame(packet, expected, frame)
    await _assert_quiet(dut, sink)


@cocotb.test()
async def test_eop_marker_is_retained_while_stage_is_stalled(dut) -> None:
    """Keep the one-shot EOP event until the staged output beat can advance."""
    await _start_clock_and_release_reset(dut)
    sink = _make_sink(dut)
    host = _make_host(dut)
    await host.bus_reset(FS_RESET_DETECT_FS)

    sink.pause = True
    packet = DataPacket(Pid.DATA0, bytes((0xA5, 0x5A)))
    recv_task = cocotb.start_soon(sink.recv(compact=False))
    await host.send_packet(packet)

    for _ in range(_POST_EOP_SETTLE_CYCLES):
        await RisingEdge(dut.clk)
    await ReadOnly()
    assert not recv_task.done(), "paused sink unexpectedly completed a frame"

    await Timer(1, unit="ps")
    sink.pause = False
    frame = await with_timeout(recv_task, 50, "us")
    assert frame.tkeep[-1] == 0, "EOP marker was lost while the output stage was stalled"
    assert frame.tdata[-1] == 0, "EOP marker must carry zero data"
    await _assert_quiet(dut, sink)


@cocotb.test()
async def test_axis_stream_consecutive_packets(dut) -> None:
    """Verify no data leakage between back-to-back packets."""
    await _start_clock_and_release_reset(dut)
    sink = _make_sink(dut)
    host = _make_host(dut)
    await host.bus_reset(FS_RESET_DETECT_FS)

    packets: list[Packet] = [
        HandshakePacket(Pid.ACK),
        DataPacket(Pid.DATA0, bytes(range(8))),
        TokenPacket(Pid.SETUP, 5, 0),
        HandshakePacket(Pid.NAK),
        DataPacket(Pid.DATA1, b""),
        DataPacket(Pid.DATA0, b"\xff"),
        DataPacket(Pid.DATA1, b"\xff" * 63),
        DataPacket(Pid.DATA0, bytes(range(64))),
        SofPacket(0x42),
        HandshakePacket(Pid.STALL),
    ]

    for i, packet in enumerate(packets):
        expected = bytes(encode_packet_bytes(packet))
        recv_task = cocotb.start_soon(_receive_frame(sink, expected))
        await host.send_packet(packet)
        frame = await recv_task
        _check_frame(packet, expected, frame, index=i)
    await _assert_quiet(dut, sink)



@cocotb.test()
async def test_tx_active_hides_the_bus(dut) -> None:
    """While the device transmits (tx_active), the receiver must not decode the bus:
    packets on the line then produce no beats and no errors. Reception works again
    as soon as tx_active falls, without a guard time, because a host may answer only
    two bit times after the device's EOP."""
    await _start_clock_and_release_reset(dut)
    sink = _make_sink(dut)
    host = _make_host(dut)
    await host.bus_reset(FS_RESET_DETECT_FS)

    hidden = (DataPacket(Pid.DATA1, bytes(range(16))), HandshakePacket(Pid.ACK),
              DataPacket(Pid.DATA0, b"\xff" * 8))
    for packet in hidden:
        await RisingEdge(dut.clk)
        dut.tx_active.value = 1
        await host.send_packet(packet)
        await RisingEdge(dut.clk)
        dut.tx_active.value = 0
        received = HandshakePacket(Pid.NAK)
        expected = bytes(encode_packet_bytes(received))
        recv_task = cocotb.start_soon(_receive_frame(sink, expected))
        await host.send_packet(received)
        frame = await recv_task
        _check_frame(received, expected, frame, hidden=repr(packet))
    await _assert_quiet(dut, sink)



class _RiseCounter:
    def __init__(self, signal, clk) -> None:
        self.rises = 0
        self._signal = signal
        self._clk = clk
        cocotb.start_soon(self._run())

    async def _run(self) -> None:
        previous = 0
        while True:
            await RisingEdge(self._clk)
            value = int(self._signal.value)
            self.rises += int(value and not previous)
            previous = value


async def _drive_se0(host: UsbFsHost, duration_fs: int) -> None:
    await host.bus.drive(LineState.SE0)
    await host.bus.port.wait_fs(duration_fs)
    await host.bus.release()


@cocotb.test()
async def test_reset_detected(dut) -> None:
    """reset_detected rises once SE0 has lasted 2.5 us and stays high until SE0 ends.
    An EOP and shorter SE0 periods never raise it, and a packet right after a reset
    is received normally."""
    await _start_clock_and_release_reset(dut)
    sink = _make_sink(dut)
    host = _make_host(dut)
    await host.bus_reset(FS_RESET_DETECT_FS)
    await Timer(10 * FS_BIT_PERIOD_FS, unit="fs")
    counter = _RiseCounter(dut.reset_detected, dut.clk)

    for duration_fs in (2 * FS_BIT_PERIOD_FS, 1_000_000_000, 2_000_000_000):
        await _drive_se0(host, duration_fs)
        await Timer(10 * FS_BIT_PERIOD_FS, unit="fs")
    packet = DataPacket(Pid.DATA0, bytes(range(8)))
    expected = bytes(encode_packet_bytes(packet))
    recv_task = cocotb.start_soon(_receive_frame(sink, expected))
    await host.send_packet(packet)
    _check_frame(packet, expected, await recv_task, case="EOP and short SE0")
    assert counter.rises == 0, f"reset_detected rose {counter.rises} time(s) for SE0 shorter than 2.5 us"

    await _drive_se0(host, 3_000_000_000)
    assert counter.rises == 1, f"reset_detected rose {counter.rises} time(s) for a 3 us SE0"
    assert int(dut.reset_detected.value) == 1, "reset_detected fell before SE0 ended"
    await Timer(10 * FS_BIT_PERIOD_FS, unit="fs")
    assert int(dut.reset_detected.value) == 0, "reset_detected stayed high after SE0 ended"

    recv_task = cocotb.start_soon(_receive_frame(sink, expected))
    await host.send_packet(packet)
    _check_frame(packet, expected, await recv_task, case="after reset")
    assert counter.rises == 1, f"reset_detected rose {counter.rises} time(s) in total, expected 1"
    await _assert_quiet(dut, sink)
