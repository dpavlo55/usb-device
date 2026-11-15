"""Black-box verification of the USB packet byte-to-bit serializer."""
from __future__ import annotations

import logging
import os
import random
from collections import deque
from dataclasses import dataclass
from fractions import Fraction
from typing import Iterable

import cocotb
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge
from cocotbext.axi import AxiStreamBus, AxiStreamFrame, AxiStreamSource

from models.usb_fs_codec import encode_packet_bytes
from models.usb_fs_types import DataPacket, HandshakePacket, Packet, Pid
from drivers.clock_reset import ClockDomain, start_clocks_in_reset, release_resets


CLOCK_PERIOD_NS = Fraction(20833, 1000)
RESET_CYCLES = 4
QUIET_CYCLES = 8
FAILURE_OBSERVATION_CYCLES = 128


@dataclass(frozen=True)
class ExpectedByte:
    value: int
    last: bool
    context: str


@dataclass(frozen=True)
class ExpectedBit:
    value: int
    last: bool
    bit_index: int
    context: str


def _seed_from_environment() -> int:
    text = os.environ.get("SEED")
    return int(text, 0) if text is not None else random.SystemRandom().getrandbits(32)


def _packet_bytes(packet: Packet) -> bytes:
    """Build exactly the bytes presented to the DUT, including USB SYNC."""
    return bytes((0x80, *encode_packet_bytes(packet)))


def _build_packets(seed: int) -> list[Packet]:
    """Create a bounded, seeded corpus of HANDSHAKE and DATA packets only."""
    rng = random.Random(seed)
    packets: list[Packet] = [
        DataPacket(Pid.DATA0, b""),
        DataPacket(Pid.DATA1, b"\xA5"),
        HandshakePacket(Pid.ACK),
        HandshakePacket(Pid.NAK),
        HandshakePacket(Pid.STALL),
    ]

    # One-byte DATA packets cover every possible byte value independently.
    packets.extend(DataPacket(Pid.DATA0, bytes((value,))) for value in range(256))
    packets.extend(
        (
            DataPacket(Pid.DATA0, bytes(32)),
            DataPacket(Pid.DATA1, bytes((0xFF,)) * 32),
            DataPacket(Pid.DATA0, bytes((0x00, 0xFF, 0x55, 0xAA)) * 16),
            DataPacket(Pid.DATA1, bytes((0xAA, 0x55)) * 32),
            DataPacket(Pid.DATA0, rng.randbytes(1023)),
        )
    )
    for index in range(12):
        length = rng.randrange(0, 129)
        pid = Pid.DATA0 if index % 2 == 0 else Pid.DATA1
        packets.append(DataPacket(pid, rng.randbytes(length)))

    rng.shuffle(packets)
    return packets


def _resolved_int(signal, name: str) -> int:
    value = signal.value
    if hasattr(value, "is_resolvable") and not value.is_resolvable:
        raise AssertionError(f"{name} is unknown at the sampling edge: {value!s}")
    text = str(value).lower()
    if any(marker in text for marker in ("x", "z", "u", "w", "-")):
        raise AssertionError(f"{name} is unknown at the sampling edge: {value!s}")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise AssertionError(f"{name} is not a two-state value: {value!s}") from exc


def _logic(signal, name: str) -> int:
    value = _resolved_int(signal, name)
    if value not in (0, 1):
        raise AssertionError(f"{name} is not one bit: {value}")
    return value


class SerializerBench:
    """Drive packet bytes and score only transfers observed at the ports."""

    def __init__(self, dut, packets: Iterable[Packet], backpressure: bool) -> None:
        self.dut = dut
        self.backpressure = backpressure
        self.packets = tuple(packets)
        self.source = AxiStreamSource(
            AxiStreamBus.from_prefix(dut, "packet"),
            dut.clk,
            dut.rst_n,
            reset_active_level=0,
        )
        self.source.log.setLevel(logging.WARNING)
        self.input_plan: deque[ExpectedByte] = deque()
        self.output_plan: deque[ExpectedBit] = deque()
        self.input_stall: tuple[int, int, int, int] | None = None
        self.output_stall: tuple[int, int, int, int] | None = None
        self.cycle = 0
        self.accepted_bytes = 0
        self.observed_output_beats = 0
        self.stalled_output_beats = 0
        self.forced_ready_low = 0

    def _enqueue_packet(self, packet_index: int, packet: Packet) -> int:
        values = _packet_bytes(packet)
        context = f"packet={packet_index} pid={packet.pid.name} input_bytes={len(values)}"
        self.input_plan.extend(
            ExpectedByte(value, byte_index == len(values) - 1, f"{context} byte={byte_index}")
            for byte_index, value in enumerate(values)
        )
        self.source.send_nowait(AxiStreamFrame(values, tdest=0))
        return len(values)

    def _accept_input_byte(self, beat: tuple[int, int, int, int]) -> None:
        data, keep, dest, last = beat
        if keep != 1 or dest != 0:
            raise AssertionError(
                f"cycle {self.cycle}: input keep/dest must be 1/0, "
                f"observed {keep}/{dest}"
            )
        if not self.input_plan:
            raise AssertionError(f"cycle {self.cycle}: unexpected input byte {data:#04x}")
        expected = self.input_plan.popleft()
        if (data, bool(last)) != (expected.value, expected.last):
            raise AssertionError(
                f"cycle {self.cycle}: {expected.context}: expected "
                f"data/last=({expected.value:#04x}, {int(expected.last)}), "
                f"observed=({data:#04x}, {last})"
            )
        for bit_index in range(8):
            self.output_plan.append(
                ExpectedBit(
                    (data >> bit_index) & 1,
                    expected.last and bit_index == 7,
                    bit_index,
                    expected.context,
                )
            )
        self.accepted_bytes += 1

    def _sample_input(self) -> None:
        valid = _logic(self.dut.packet_tvalid, "packet_tvalid")
        ready = _logic(self.dut.packet_tready, "packet_tready")
        if not valid:
            if self.input_stall is not None:
                raise AssertionError(f"cycle {self.cycle}: input TVALID dropped while stalled")
            return

        beat = tuple(
            _resolved_int(getattr(self.dut, f"packet_t{field}"), f"packet_t{field}")
            for field in ("data", "keep", "dest", "last")
        )
        if self.input_stall is not None and beat != self.input_stall:
            raise AssertionError(
                f"cycle {self.cycle}: input beat changed while stalled: "
                f"{self.input_stall!r} -> {beat!r}"
            )
        if ready:
            self._accept_input_byte(beat)
            self.input_stall = None
        else:
            if not self.input_plan:
                raise AssertionError(f"cycle {self.cycle}: unplanned stalled input beat {beat!r}")
            self.input_stall = beat

    def _sample_output(self) -> tuple[int, int]:
        valid = _logic(self.dut.stream_tvalid, "stream_tvalid")
        ready = _logic(self.dut.stream_tready, "stream_tready")
        if not self.backpressure:
            assert ready == 1, "stream_tready must stay high without backpressure"
        if not valid:
            if self.output_stall is not None:
                raise AssertionError(f"cycle {self.cycle}: output TVALID dropped while stalled")
            return valid, ready
        beat = (
            _logic(self.dut.stream_tdata, "stream_tdata"),
            _logic(self.dut.stream_tkeep, "stream_tkeep"),
            _logic(self.dut.stream_tdest, "stream_tdest"),
            _logic(self.dut.stream_tlast, "stream_tlast"),
        )
        if self.output_stall is not None and beat != self.output_stall:
            raise AssertionError(
                f"cycle {self.cycle}: output beat changed while stalled: "
                f"{self.output_stall!r} -> {beat!r}"
            )
        if not self.output_plan:
            raise AssertionError(f"cycle {self.cycle}: unexpected output beat {beat!r}")
        expected = self.output_plan[0]
        expected_beat = (expected.value, 1, 0, int(expected.last))
        if beat != expected_beat:
            raise AssertionError(
                f"cycle {self.cycle}: {expected.context} bit={expected.bit_index}: "
                f"expected output {expected_beat!r}, observed {beat!r}"
            )
        if ready:
            self.observed_output_beats += 1
            self.output_plan.popleft()
            self.output_stall = None
        else:
            self.output_stall = beat
            self.stalled_output_beats += 1
        return valid, ready

    def _next_ready(self, output_valid: int, output_ready: int) -> int:
        if not self.backpressure:
            return 1
        if not self.input_plan and not self.output_plan and self.source.idle():
            return 1
        if self.forced_ready_low:
            self.forced_ready_low -= 1
            return 0
        phase = self.cycle % 41
        if phase in (5, 6, 7, 8, 9, 25, 26, 27):
            return 0
        # Deliberately create deterministic stalls at byte and packet boundaries.
        if output_valid and output_ready and self.output_plan:
            next_bit = self.output_plan[0]
            if next_bit.bit_index == 0 or next_bit.last:
                self.forced_ready_low = 3
                return 0
        return 1

    async def run(self) -> None:
        try:
            for packet_index, packet in enumerate(self.packets):
                await self._run_packet(packet_index, packet)
            if self.backpressure and not self.stalled_output_beats:
                raise AssertionError("backpressure test did not stall a valid output beat")
        except AssertionError as exc:
            cocotb.log.error("Serializer verification failed: %s", exc)
            cocotb.log.info(
                "Continuing for %d clocks for waveform inspection, with "
                "stream_tready=1 and no new packets, then failing with the original error",
                FAILURE_OBSERVATION_CYCLES,
            )
            # Keep the current source frame and clock running without resetting
            # the DUT or relying on a scoreboard that may now be out of sync.
            for _ in range(FAILURE_OBSERVATION_CYCLES):
                await FallingEdge(self.dut.clk)
                self.dut.stream_tready.value = 1
                await RisingEdge(self.dut.clk)
                self.cycle += 1
            await ReadOnly()
            raise

    async def _run_packet(self, packet_index: int, packet: Packet) -> None:
        """Require this packet's entire serialized stream before sending another."""
        packet_bytes = self._enqueue_packet(packet_index, packet)
        context = f"packet={packet_index} pid={packet.pid.name}"
        input_start = self.accepted_bytes
        output_start = self.observed_output_beats
        # Give each packet its own bound, including time spent under backpressure.
        max_cycles = packet_bytes * (64 if self.backpressure else 32) + 512
        quiet = 0
        for _ in range(max_cycles):
            await RisingEdge(self.dut.clk)
            self.cycle += 1

            # Samples precede ReadOnly and testbench writes: both handshakes
            # describe this same clock edge.
            self._sample_input()
            output_valid, output_ready = self._sample_output()

            complete = (
                not self.input_plan
                and self.source.idle()
                and not self.output_plan
                and self.output_stall is None
            )
            quiet = quiet + 1 if complete and not output_valid else 0
            if complete and quiet >= QUIET_CYCLES:
                accepted = self.accepted_bytes - input_start
                observed = self.observed_output_beats - output_start
                if accepted != packet_bytes:
                    raise AssertionError(
                        f"{context}: accepted byte count {accepted} != {packet_bytes}"
                    )
                if observed != packet_bytes * 8:
                    raise AssertionError(
                        f"{context}: serialized output count {observed} != "
                        f"eight per byte ({packet_bytes * 8})"
                    )
                # Leave the transfer edge before the next packet is submitted.
                await FallingEdge(self.dut.clk)
                return

            next_ready = self._next_ready(output_valid, output_ready)
            # Drive away from the transfer edge to avoid racing the RTL.
            await FallingEdge(self.dut.clk)
            self.dut.stream_tready.value = next_ready
        observed = self.observed_output_beats - output_start
        reason = "no serialized stream received" if observed == 0 else "incomplete serialized stream"
        raise AssertionError(
            f"{context}: {reason} within {max_cycles} cycles: "
            f"accepted_bytes={self.accepted_bytes - input_start}/{packet_bytes}, "
            f"serialized_bits={observed}/{packet_bytes * 8}, "
            f"pending_bits={len(self.output_plan)}, last_stall={self.output_stall!r}"
        )


async def _start(dut, backpressure: bool) -> SerializerBench:
    seed = _seed_from_environment()
    packets = _build_packets(seed)
    cocotb.log.info(
        "serializer seed=%d (0x%X) backpressure=%s packet_count=%d",
        seed,
        seed,
        backpressure,
        len(packets),
    )
    dut.stream_tready.value = 1
    bench = SerializerBench(dut, packets, backpressure)
    domain = ClockDomain(dut.clk, dut.rst_n, CLOCK_PERIOD_NS)
    await start_clocks_in_reset(domain)
    for _ in range(RESET_CYCLES):
        await RisingEdge(dut.clk)
        if _logic(dut.stream_tready, "stream_tready") != 1:
            raise AssertionError("stream_tready must remain high during reset")
        await ReadOnly()
        assert _logic(dut.stream_tvalid, "stream_tvalid") == 0, "TVALID asserted during reset"
    await release_resets(domain)
    return bench


@cocotb.test()
async def test_without_backpressure(dut) -> None:
    """Serialize a seeded corpus while stream_tready remains continuously high."""
    bench = await _start(dut, backpressure=False)
    await bench.run()


@cocotb.test()
async def test_with_backpressure(dut) -> None:
    """Serialize a seeded corpus with deterministic, bounded output stalls."""
    bench = await _start(dut, backpressure=True)
    await bench.run()