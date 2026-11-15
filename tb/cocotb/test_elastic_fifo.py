"""Black-box ready/valid verification for ``elastic_fifo``.

The model below is deliberately only a deque of accepted stream beats.  It
does not reproduce a pointer, memory, or output-register implementation.
The contract is a stored (non-bypass) FIFO with asynchronous active-low reset.
"""
from __future__ import annotations

import os
import random
from collections import deque
from typing import NamedTuple

import cocotb
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge, Timer
from drivers.clock_reset import ClockDomain, start_clocks_in_reset, release_resets


SUPPORTED_ADDR_WIDTHS = (1, 2, 3, 4, 5)
try:
    ADDR_WIDTH = int(os.getenv("ELASTIC_FIFO_ADDR_WIDTH", "2"), 0)
except ValueError as exc:
    raise ValueError("ELASTIC_FIFO_ADDR_WIDTH must be one of 1, 2, 3, 4, or 5") from exc
if ADDR_WIDTH not in SUPPORTED_ADDR_WIDTHS:
    raise ValueError(f"ELASTIC_FIFO_ADDR_WIDTH={ADDR_WIDTH} is not supported")
MEMORY_DEPTH = 1 << ADDR_WIDTH
DEPTH = MEMORY_DEPTH + 2  # FIFO memory plus the input and output buffer slots.

SEED = int(os.getenv("SEED", "0x1B00F"), 0)
MAX_CYCLES = 4096


class Beat(NamedTuple):
    data: int
    keep: int
    dest: int
    last: int


class StepResult(NamedTuple):
    input_handshake: bool
    output_handshake: bool
    output_beat: Beat | None


def _beat(dut, prefix: str) -> Beat:
    return Beat(*(int(getattr(dut, f"{prefix}{field}").value)
                  for field in ("data", "keep", "dest", "last")))


def _drive(dut, beat: Beat) -> None:
    for field, value in zip(Beat._fields, beat):
        signal = getattr(dut, f"i{field}")
        signal.value = value & ((1 << len(signal)) - 1)


def _sample(dut) -> tuple[Beat, int, int, Beat | None, int, int]:
    # Invalid output payload is unspecified and may legally contain U/X bits.
    ovalid = int(dut.ovalid.value)
    iready = int(dut.iready.value) if int(dut.rst_n.value) else 0
    return (_beat(dut, "i"), int(dut.ivalid.value), iready,
            _beat(dut, "o") if ovalid else None, ovalid, int(dut.oready.value))


class FifoModel:
    """Pre-edge ready/valid monitor and specification-level FIFO model."""

    def __init__(self, depth: int) -> None:
        self.depth = depth
        self.expected: deque[Beat] = deque()
        self.accepted = 0
        self.delivered = 0
        self.sidebands: set[tuple[int, int, int]] = set()
        self.data_seen: set[int] = set()
        self.occupancies: set[int] = set()
        self.handshakes: set[tuple[bool, bool]] = set()
        self.input_stalls = self.output_stalls = 0
        self._previous_input: tuple[Beat, int, int] | None = None
        self._previous_output: tuple[Beat | None, int, int] | None = None

    def _check_stability(self, current) -> None:
        ibeat, ivalid, iready, obeat, ovalid, oready = current
        if self._previous_input is not None:
            old_beat, old_valid, old_ready = self._previous_input
            if old_valid and not old_ready:
                assert (ibeat, ivalid) == (old_beat, old_valid), (
                    "source changed beat or VALID while waiting for IREADY"
                )
        if self._previous_output is not None:
            old_beat, old_valid, old_ready = self._previous_output
            if old_valid and not old_ready:
                assert (obeat, ovalid) == (old_beat, old_valid), (
                    "output changed beat or VALID during backpressure"
                )

    def _check_visible(self, current) -> None:
        ibeat, ivalid, iready, obeat, ovalid, oready = current
        del ibeat, ivalid
        if self.expected:
            # A newly accepted beat takes an extra cycle through the output buffer.
            if ovalid:
                assert obeat == self.expected[0], (
                    f"output mismatch: observed={obeat}, expected={self.expected[0]}"
                )
        else:
            assert ovalid == 0, "empty FIFO produced a spurious OVALID"
        if len(self.expected) == self.depth and iready:
            assert ovalid and oready, (
                "full FIFO asserted IREADY without a simultaneous output handshake"
            )

    async def step(self, dut, cycle: int) -> StepResult:
        """Sample before the rising edge, then validate the settled next state."""
        await FallingEdge(dut.clk)
        await ReadOnly()
        current = _sample(dut)
        reset = int(dut.rst_n.value) == 0
        if reset:
            self.expected.clear()
            self.accepted = self.delivered = 0
            self._previous_input = self._previous_output = None
        else:
            self._check_stability(current)
            self._check_visible(current)
            self.occupancies.add(len(self.expected))
        _, ivalid, iready, obeat, ovalid, oready = current
        input_handshake = bool(not reset and ivalid and iready)
        output_handshake = bool(not reset and ovalid and oready)
        if not reset:
            self.handshakes.add((input_handshake, output_handshake))
            self.input_stalls += bool(ivalid and not iready)
            self.output_stalls += bool(ovalid and not oready)
        if input_handshake:
            self.expected.append(_beat(dut, "i"))
            self.accepted += 1
        if output_handshake:
            assert self.expected, f"cycle={cycle}: output handshake without an input"
            expected = self.expected.popleft()
            assert obeat == expected
            self.delivered += 1
            self.sidebands.add(expected[1:])
            self.data_seen.add(expected.data)
        assert len(self.expected) <= self.depth, f"cycle={cycle}: FIFO overflow"

        await RisingEdge(dut.clk)
        await ReadOnly()
        post = _sample(dut)
        post_reset = int(dut.rst_n.value) == 0
        if post_reset:
            self.expected.clear()
            self.accepted = self.delivered = 0
            assert post[4] == 0, "reset did not clear OVALID after a clocked edge"
            self._previous_input = self._previous_output = None
        else:
            if post[4]:
                assert self.expected and post[3] == self.expected[0], (
                    f"cycle={cycle}: settled output does not show FIFO head"
                )
            elif not self.expected:
                assert post[4] == 0, "settled empty FIFO still asserts OVALID"
            if not reset and ovalid and not oready:
                assert (post[3], post[4]) == (obeat, ovalid), (
                    "output changed on an edge while OREADY was low"
                )
            self._previous_input = (current[0], ivalid, iready)
            self._previous_output = (obeat, ovalid, oready)
        await Timer(1, unit="ps")
        return StepResult(input_handshake, output_handshake,
                          obeat if output_handshake else None)

    def assert_empty(self) -> None:
        assert not self.expected
        assert self.accepted == self.delivered


async def _initial_reset(dut) -> None:
    """Shared initial sequence (reset 1 -> 0 before the first edge), plus the reset check."""
    dut.ivalid.value = 0
    dut.oready.value = 0
    _drive(dut, Beat(0, 0, 0, 0))
    domain = ClockDomain(dut.clk, dut.rst_n, 10)
    await start_clocks_in_reset(domain)
    for _ in range(2):
        await RisingEdge(dut.clk)
    await ReadOnly()
    assert int(dut.ovalid.value) == 0, "reset did not clear output VALID"
    await release_resets(domain)
    await RisingEdge(dut.clk)
    await ReadOnly()
    await Timer(1, unit="ps")


async def _assert_async_reset(dut) -> None:
    """Assert between edges and check reset before any subsequent rising edge."""
    await FallingEdge(dut.clk)
    await Timer(1, unit="ns")
    dut.rst_n.value = 0
    await Timer(1, unit="ps")
    await ReadOnly()
    assert int(dut.clk.value) == 0, "reset check must precede the next rising edge"
    assert str(dut.ovalid.value) == "0", (
        f"asynchronous reset did not clear OVALID before a rising edge: {dut.ovalid.value}"
    )
    await Timer(1, unit="ps")


async def _reset(dut) -> None:
    dut.rst_n.value = 1
    dut.ivalid.value = 0
    dut.oready.value = 0
    _drive(dut, Beat(0, 0, 0, 0))
    await _assert_async_reset(dut)
    for _ in range(2):
        await RisingEdge(dut.clk)
    await ReadOnly()
    assert int(dut.ovalid.value) == 0, "reset did not clear output VALID"
    await Timer(1, unit="ps")
    dut.rst_n.value = 1
    await RisingEdge(dut.clk)
    await ReadOnly()
    await Timer(1, unit="ps")


async def _send_sequence(dut, model: FifoModel, beats: list[Beat],
                         ready_fn, start_cycle: int = 0) -> int:
    index = 0
    cycle = start_cycle
    dut.ivalid.value = int(bool(beats))
    if beats:
        _drive(dut, beats[0])
    while index < len(beats) or model.expected:
        dut.oready.value = int(ready_fn(cycle, len(model.expected)))
        result = await model.step(dut, cycle)
        if result.input_handshake:
            index += 1
            dut.ivalid.value = int(index < len(beats))
            if index < len(beats):
                _drive(dut, beats[index])
        cycle += 1
        assert cycle - start_cycle < MAX_CYCLES, "sequence did not make progress"
    return cycle


@cocotb.test()
async def test_elastic_fifo_empty_and_single_beat(dut) -> None:
    """Invalid input noise cannot create output; one beat obeys stored FIFO timing."""
    await _initial_reset(dut)
    model = FifoModel(DEPTH)
    dut.oready.value = 0
    for value in range(4):
        dut.ivalid.value = 0
        dut.oready.value = value & 1
        _drive(dut, Beat(value, value & 1, value & 0xF, value & 1))
        result = await model.step(dut, value)
        assert not result.output_handshake
    dut.oready.value = 0
    offered = Beat(0xA6, 0, 0xD, 1)
    _drive(dut, offered)
    dut.ivalid.value = 1
    result = await model.step(dut, 4)
    assert result.input_handshake and not result.output_handshake
    dut.ivalid.value = 0
    result = await model.step(dut, 5)
    assert not result.output_handshake
    dut.oready.value = 1
    result = await model.step(dut, 6)
    assert result.output_handshake and result.output_beat == offered
    for cycle in range(7, 10):
        await model.step(dut, cycle)
    model.assert_empty()


@cocotb.test()
async def test_elastic_fifo_capacity_and_all_sidebands(dut) -> None:
    """Fill exactly to capacity, exercise full backpressure, and cover payloads."""
    await _initial_reset(dut)
    model = FifoModel(DEPTH)
    dut.oready.value = 0
    dut.ivalid.value = 1
    for index in range(DEPTH):
        _drive(dut, Beat(index, index & 1, (index >> 2) & 0xF, (index >> 1) & 1))
        result = await model.step(dut, index)
        assert result.input_handshake
    assert len(model.expected) == DEPTH
    pending = Beat(0xEE, 1, 0xF, 1)
    _drive(dut, pending)
    for cycle in range(DEPTH, DEPTH + 2 * DEPTH + 16):
        result = await model.step(dut, cycle)
        assert not result.input_handshake and not result.output_handshake
    dut.oready.value = 1
    result = await model.step(dut, DEPTH + 1)
    # Conservative full-ready and look-ahead-ready implementations are both valid.
    if result.input_handshake:
        dut.ivalid.value = 0
    else:
        assert int(dut.ivalid.value) == 1
    for cycle in range(DEPTH + 2, DEPTH * 4 + 8):
        result = await model.step(dut, cycle)
        if result.input_handshake:
            dut.ivalid.value = 0
        if not model.expected and not dut.ivalid.value:
            break
    model.assert_empty()

    beats = [Beat(data, index & 1, (index >> 2) & 0xF, (index >> 1) & 1)
             for index, data in enumerate(range(256))]
    await _send_sequence(dut, model, beats,
                         lambda cycle, occupancy: cycle % 17 >= 5)
    # Equal DATA cannot hide sideband changes; equal complete beats still count.
    await _send_sequence(
        dut, model,
        [Beat(0xA5, index & 1, (index >> 2) & 0xF, (index >> 1) & 1)
         for index in range(64)] + [Beat(0xA5, 0, 0xF, 1)] * (DEPTH * 3),
        lambda cycle, occupancy: cycle % 11 >= 3,
    )
    model.assert_empty()
    assert model.data_seen == set(range(256)), "not all 8-bit data values were delivered"
    assert len(model.sidebands) == 64, "not all KEEP/DEST/LAST combinations were delivered"
    assert (0, 0xF, 1) in model.sidebands, "KEEP=0/LAST=1 corner was not observed"


@cocotb.test()
async def test_elastic_fifo_wrap_streams_and_simultaneous_transfers(dut) -> None:
    """Wrap pointers repeatedly and prove LAST is metadata, not FIFO control."""
    await _initial_reset(dut)
    model = FifoModel(DEPTH)
    # Repeated exact fill/drain cycles cross every pointer-wrap boundary.
    for run in range(12):
        dut.oready.value = 0
        dut.ivalid.value = 1
        for index in range(DEPTH):
            _drive(dut, Beat((run * 37 + index) & 0xFF, index & 1, run, run & 1))
            result = await model.step(dut, run * 2 * DEPTH + index)
            assert result.input_handshake and not result.output_handshake
        dut.ivalid.value = 0
        for stall in range(3):
            _drive(dut, Beat(stall * 73, stall & 1, 0xF, 1))
            await model.step(dut, stall)
        dut.oready.value = 1
        for index in range(DEPTH):
            result = await model.step(dut, index)
            assert result.output_handshake and not result.input_handshake
        await model.step(dut, DEPTH)
        model.assert_empty()
    assert model.occupancies == set(range(DEPTH + 1))
    prefill = max(1, DEPTH // 2)
    dut.oready.value = 0
    dut.ivalid.value = 1
    for index in range(prefill):
        _drive(dut, Beat(0x40 + index, 1, index & 0xF, 0))
        result = await model.step(dut, index)
        assert result.input_handshake
    for cycle in range(8 * DEPTH + 64):
        before = len(model.expected)
        _drive(dut, Beat((0x80 + cycle) & 0xFF, 1, cycle & 0xF, 0))
        dut.oready.value = 1
        result = await model.step(dut, prefill + cycle)
        if 0 < before < DEPTH and result.output_handshake:
            assert result.input_handshake, (
                "intermediate occupancy did not sustain one beat per clock"
            )
    dut.ivalid.value = 0
    await _send_sequence(
        dut, model,
        [Beat(index & 0xFF, index & 1, index & 0xF, 0)
         for index in range(DEPTH * 5 + 3)],
        lambda cycle, occupancy: cycle % 9 not in (0, 1, 2),
    )
    for run, length in enumerate((1, DEPTH + 1, DEPTH * 2 + 3)):
        await _send_sequence(
            dut, model,
            [Beat((run * 37 + index) & 0xFF, 1, run, 1)
             for index in range(length)],
            lambda cycle, occupancy: cycle % 7 != 0,
        )
    model.assert_empty()


@cocotb.test()
async def test_elastic_fifo_reset_flushes_every_occupancy(dut) -> None:
    """Reset at every occupancy with live traffic, then accept distinct data."""
    await _initial_reset(dut)
    for occupancy in range(DEPTH + 1):
        await _reset(dut)
        model = FifoModel(DEPTH)
        # Reset with wrapped, nonzero read/write pointers as well as every fill level.
        await _send_sequence(
            dut, model, [Beat(index & 0xFF, 1, 3, 0) for index in range(DEPTH + 3)],
            lambda cycle, pending: True,
        )
        dut.oready.value = 0
        dut.ivalid.value = 1
        for index in range(occupancy):
            _drive(dut, Beat(0x20 + index, 1, index, 0))
            result = await model.step(dut, index)
            assert result.input_handshake
        _drive(dut, Beat(0xE0 + occupancy, 0, 0xF, 1))
        dut.ivalid.value = 1
        dut.oready.value = occupancy & 1
        await _assert_async_reset(dut)
        for cycle in range(2):
            await model.step(dut, 100 + cycle)
        assert not model.expected
        await Timer(1, unit="ps")
        dut.rst_n.value = 1
        dut.ivalid.value = 0
        dut.oready.value = 1
        for cycle in range(2):
            result = await model.step(dut, 110 + cycle)
            assert not result.output_handshake
        postreset = Beat(0x5A + occupancy, 0, 0xA, 1)
        _drive(dut, postreset)
        dut.ivalid.value = 1
        result = await model.step(dut, 120)
        assert result.input_handshake and not result.output_handshake
        dut.ivalid.value = 0
        result = await model.step(dut, 121)
        assert not result.output_handshake
        result = await model.step(dut, 122)
        assert result.output_handshake and result.output_beat == postreset
        model.assert_empty()

        # A short reset pulse entirely between rising edges must also flush data.
        dut.oready.value = 0
        dut.ivalid.value = 1
        for index in range(occupancy):
            _drive(dut, Beat(0x80 + index, 0, index & 0xF, 1))
            assert (await model.step(dut, 130 + index)).input_handshake
        _drive(dut, Beat(0xF0, 1, 0xF, 0))
        await _assert_async_reset(dut)
        await Timer(1, unit="ns")
        assert int(dut.clk.value) == 0, "short reset pulse crossed a rising edge"
        dut.rst_n.value = 1
        dut.ivalid.value = 0
        dut.oready.value = 1
        model = FifoModel(DEPTH)  # Reset intentionally cancels old expectations.
        for cycle in range(2):
            result = await model.step(dut, 170 + cycle)
            assert not result.output_handshake
        await _send_sequence(dut, model, [Beat(0xCA, 1, 0x5, 0)],
                             lambda cycle, pending: True)
        model.assert_empty()


def _random_beat(dut, rng: random.Random) -> Beat:
    return Beat(*(rng.getrandbits(len(getattr(dut, f"i{field}")))
                  for field in Beat._fields))


@cocotb.test()
async def test_elastic_fifo_bursty_random_ready_valid(dut) -> None:
    """Bursty deterministic traffic holds pending source beats and forces progress."""
    await _initial_reset(dut)
    cocotb.log.info("elastic_fifo random seed=0x%X addr_width=%d depth=%d",
                    SEED, ADDR_WIDTH, DEPTH)
    rng = random.Random(SEED)
    model = FifoModel(DEPTH)
    target = 2000
    sent = 0
    cycle = 0
    pending: Beat | None = None
    ready = False
    burst_left = 0
    dut.ivalid.value = 0
    while sent < target or pending is not None or model.expected:
        if pending is None and sent < target and (rng.random() < 0.72 or cycle % 5 == 0):
            pending = _random_beat(dut, rng)
            _drive(dut, pending)
        elif pending is None:
            # VALID=0 payload noise must never create a stored beat.
            _drive(dut, _random_beat(dut, rng))
        dut.ivalid.value = int(pending is not None)
        if burst_left == 0:
            ready = bool(rng.getrandbits(1))
            burst_left = rng.randint(1, 2 * DEPTH + 8)
        burst_left -= 1
        # Periodic guaranteed progress also bounds the final drain.
        dut.oready.value = int(ready or cycle % (4 * DEPTH + 32) == 0 or sent == target)
        result = await model.step(dut, cycle)
        if result.input_handshake:
            sent += 1
            pending = None
            dut.ivalid.value = 0
        cycle += 1
        assert cycle < target * 20 + DEPTH * 8, (
            f"bursty traffic timed out: sent={sent} pending={len(model.expected)}"
        )
    model.assert_empty()
    assert sent == target
    assert model.delivered == target
    assert model.occupancies == set(range(DEPTH + 1)), "not every fill level was exercised"
    assert len(model.handshakes) == 4, "not every push/pop combination was exercised"
    assert model.input_stalls and model.output_stalls, "missing source or sink backpressure"
    for extra in range(DEPTH + 2):
        await model.step(dut, cycle + extra)
    model.assert_empty()
    cocotb.log.info("elastic_fifo random test passed: cycles=%d transfers=%d", cycle, sent)