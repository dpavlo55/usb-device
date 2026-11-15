"""AXI4-Stream verification for the input/output buffer pair at default widths."""
from __future__ import annotations

import os
import random
from typing import NamedTuple

import cocotb
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge, Timer
from drivers.clock_reset import ClockDomain, start_clocks_in_reset, release_resets


NUM_DATA_VALID_PAIRS = 1000
SEED = int(os.getenv("SEED", "0x1B00F"), 0)
MAX_CYCLES = NUM_DATA_VALID_PAIRS * 20


class AxisBeat(NamedTuple):
    data: int
    keep: int
    dest: int
    last: int


StreamSample = tuple[AxisBeat, int, int]


def _beat(data: int, index: int) -> AxisBeat:
    """Cover all 64 KEEP/DEST/LAST combinations without changing the data RNG."""
    return AxisBeat(data, index & 1, (index >> 2) & 0xF, (index >> 1) & 1)


def _drive_beat(dut, beat: AxisBeat) -> None:
    for field, value in zip(AxisBeat._fields, beat):
        getattr(dut, f"i{field}").value = value


def _sample_streams(dut) -> dict[str, StreamSample]:
    return {
        prefix: (
            AxisBeat(*(int(getattr(dut, f"{prefix}{field}").value)
                       for field in AxisBeat._fields)),
            int(getattr(dut, f"{prefix}valid").value),
            int(getattr(dut, f"{prefix}ready").value),
        )
        for prefix in ("i", "c", "o")
    }


class ReadyValidScoreboard:
    """Queue input handshakes and compare output handshakes in order."""

    def __init__(self, name: str = "input/output") -> None:
        self.name = name
        self.expected: list[AxisBeat] = []
        self.input_handshakes = 0
        self.output_handshakes = 0

    def observe_input(self, beat: AxisBeat, cycle: int) -> None:
        self.expected.append(beat)
        self.input_handshakes += 1
        cocotb.log.debug(
            "%s cycle=%d input handshake beat=%s pending=%d",
            self.name,
            cycle,
            beat,
            len(self.expected),
        )

    def check_visible(self, beat: AxisBeat, cycle: int) -> None:
        assert self.expected, (
            f"{self.name} cycle={cycle}: valid beat has no preceding input handshake; "
            f"observed {beat}"
        )
        assert beat == self.expected[0], (
            f"{self.name} cycle={cycle}: AXI4-Stream beat mismatch: "
            f"observed {beat}, expected {self.expected[0]}"
        )

    def observe_output(self, beat: AxisBeat, cycle: int) -> None:
        self.check_visible(beat, cycle)
        self.expected.pop(0)
        self.output_handshakes += 1
        cocotb.log.debug(
            "%s cycle=%d output handshake beat=%s pending=%d",
            self.name,
            cycle,
            beat,
            len(self.expected),
        )


class BufferMonitor:
    """Check both stages, including visible beats throughout backpressure."""

    def __init__(self) -> None:
        self.scoreboard = ReadyValidScoreboard()
        self.core_scoreboard = ReadyValidScoreboard("input/core")
        self.previous: dict[str, StreamSample] = {}
        self.sidebands: set[tuple[int, int, int]] = set()

    def observe(self, streams: dict[str, StreamSample], cycle: int) -> None:
        for prefix, (beat, valid, ready) in streams.items():
            previous = self.previous.get(prefix)
            if previous is not None and previous[1] and not previous[2]:
                assert (beat, valid) == previous[:2], (
                    f"cycle={cycle}: {prefix}data/keep/dest/last/valid changed "
                    f"while stalled: previous={previous}, current={streams[prefix]}"
                )

        ibeat, ivalid, iready = streams["i"]
        cbeat, cvalid, cready = streams["c"]
        obeat, ovalid, oready = streams["o"]
        assert iready == int(not self.core_scoreboard.expected), (
            f"cycle={cycle}: iready does not reflect input buffer occupancy"
        )
        output_pending = len(self.scoreboard.expected) - len(self.core_scoreboard.expected)
        assert output_pending in (0, 1), f"cycle={cycle}: output buffer overflow"
        assert ovalid == int(bool(output_pending)), (
            f"cycle={cycle}: ovalid does not reflect output buffer occupancy"
        )
        assert cready == int(not ovalid or oready), (
            f"cycle={cycle}: cready must accept a beat when output is empty or ready"
        )
        assert cvalid == int(bool(self.core_scoreboard.expected) or bool(ivalid)), (
            f"cycle={cycle}: cvalid dropped a buffered or bypassed valid beat"
        )
        if iready:
            assert (cbeat, cvalid) == (ibeat, ivalid), (
                f"cycle={cycle}: input buffer bypass did not pass all AXI4-Stream signals"
            )
        if ivalid and iready:
            self.scoreboard.observe_input(ibeat, cycle)
            self.core_scoreboard.observe_input(ibeat, cycle)
            self.sidebands.add(ibeat[1:])
        if cvalid:
            self.core_scoreboard.check_visible(cbeat, cycle)
            if cready:
                self.core_scoreboard.observe_output(cbeat, cycle)
        if ovalid:
            self.scoreboard.check_visible(obeat, cycle)
            if oready:
                self.scoreboard.observe_output(obeat, cycle)
        self.previous = streams

    def assert_drained(self, count: int) -> None:
        for scoreboard in (self.scoreboard, self.core_scoreboard):
            assert scoreboard.input_handshakes == count
            assert scoreboard.output_handshakes == count
            assert not scoreboard.expected


def _check_reset(dut) -> None:
    assert int(dut.iready.value) == 1
    assert int(dut.cready.value) == 1
    assert int(dut.cvalid.value) == 0
    assert int(dut.ovalid.value) == 0
    assert all(int(getattr(dut, f"o{field}").value) == 0
               for field in AxisBeat._fields), "Output payload/sidebands did not reset"


async def _reset(dut) -> None:
    _drive_beat(dut, AxisBeat(0, 0, 0, 0))
    dut.ivalid.value = 0
    dut.oready.value = 0
    domain = ClockDomain(dut.clk, dut.rst_n, 10)
    await start_clocks_in_reset(domain)
    for _ in range(2):
        await RisingEdge(dut.clk)
    await ReadOnly()
    _check_reset(dut)
    await release_resets(domain)
    await RisingEdge(dut.clk)
    await ReadOnly()
    await Timer(1, unit="ps")


@cocotb.test()
async def test_input_output_buffer_ready_valid(dut) -> None:
    """Verify lossless ordered AXI4-Stream transfer under randomized backpressure."""
    await _reset(dut)

    rng = random.Random(SEED)
    monitor = BufferMonitor()
    scoreboard = monitor.scoreboard
    next_data = rng.randrange(256)
    valid = True
    valid_gap = 0
    previous_inputs: StreamSample | None = None

    # Begin driving transactions only immediately after a rising edge.
    await Timer(1, unit="ps")
    _drive_beat(dut, _beat(next_data, 0))
    dut.ivalid.value = 1

    cycle = 0
    while True:
        await FallingEdge(dut.clk)
        await ReadOnly()

        streams = _sample_streams(dut)
        current_beat, current_valid, current_ready = streams["i"]
        if previous_inputs is not None and previous_inputs[2] == 0:
            assert (current_beat, current_valid) == previous_inputs[:2], (
                f"cycle={cycle}: input beat/ivalid changed while iready was low; "
                f"previous={previous_inputs}, current={streams['i']}"
            )

        input_handshake = bool(current_valid and current_ready)
        await RisingEdge(dut.clk)
        # ReadOnly observes the settled post-edge DUT state.  The handshake
        # decisions use the values captured before this edge above.
        await ReadOnly()
        monitor.observe(streams, cycle)

        # Move to a writable phase before driving the next-cycle signals.
        await Timer(1, unit="ps")
        if input_handshake:
            assert scoreboard.input_handshakes <= NUM_DATA_VALID_PAIRS
            if scoreboard.input_handshakes < NUM_DATA_VALID_PAIRS:
                # The full beat changes only after the input handshake, then stays
                # unchanged through any randomized valid-low gap or backpressure.
                next_data = rng.randrange(256)
                _drive_beat(dut, _beat(next_data, scoreboard.input_handshakes))
                # Favor consecutive transfers: valid is asserted two thirds
                # of the time and deasserted one third of the time.
                valid = rng.choices((True, False), weights=(2, 1), k=1)[0]
                dut.ivalid.value = int(valid)
                valid_gap = rng.randint(1, 3) if not valid else 0
            else:
                dut.ivalid.value = 0
        elif not int(dut.ivalid.value) and scoreboard.input_handshakes < NUM_DATA_VALID_PAIRS:
            if valid_gap:
                valid_gap -= 1
            if valid_gap == 0 and current_ready:
                valid = True
                dut.ivalid.value = 1

        # Change oready only after a rising edge.  cready is combinationally
        # derived from oready and ovalid, so this keeps both synchronized.
        dut.oready.value = rng.choices((1, 0), weights=(2, 1), k=1)[0]
        previous_inputs = streams["i"]
        if (
            scoreboard.input_handshakes == NUM_DATA_VALID_PAIRS
            and not scoreboard.expected
        ):
            break
        cycle += 1
        assert cycle < MAX_CYCLES, (
            f"Transfer timed out: {scoreboard.input_handshakes} accepted, "
            f"{scoreboard.output_handshakes} delivered, pending={scoreboard.expected}"
        )

    assert scoreboard.input_handshakes == NUM_DATA_VALID_PAIRS
    for drain_cycle in range(2):
        await FallingEdge(dut.clk)
        await ReadOnly()
        streams = _sample_streams(dut)
        await RisingEdge(dut.clk)
        await ReadOnly()
        monitor.observe(streams, cycle + 1 + drain_cycle)
        # Force ready high after the first drain edge so an unexpected
        # trailing valid cannot remain hidden behind backpressure.
        if drain_cycle == 0:
            await Timer(1, unit="ps")
            dut.oready.value = 1

    monitor.assert_drained(NUM_DATA_VALID_PAIRS)
    assert len(monitor.sidebands) == 64, "Not all default-width sidebands were transferred"
    assert int(dut.ovalid.value) == 0, "Unexpected trailing output valid"
    cocotb.log.info(
        "buffer ready/valid test passed: seed=0x%X pairs=%d cycles=%d",
        SEED,
        NUM_DATA_VALID_PAIRS,
        cycle + 1,
    )


async def _step(dut, monitor: BufferMonitor, cycle: int) -> dict[str, StreamSample]:
    """Sample before the active edge, then return in a writable phase."""
    await FallingEdge(dut.clk)
    await ReadOnly()
    streams = _sample_streams(dut)
    await RisingEdge(dut.clk)
    await ReadOnly()
    monitor.observe(streams, cycle)
    await Timer(1, unit="ps")
    return streams


@cocotb.test()
async def test_input_output_buffer_continuous_valid(dut) -> None:
    """Keep TVALID high and advance beats only on handshakes under sustained load."""
    await _reset(dut)
    monitor = BufferMonitor()
    scoreboard = monitor.scoreboard
    offered = _beat(0x35, 0)
    _drive_beat(dut, offered)
    dut.ivalid.value = 1
    stalled_cycles = consecutive_transfers = 0
    max_stalled_cycles = max_consecutive_transfers = 0
    stall_episodes = simultaneous_transfers = 0

    for cycle in range(MAX_CYCLES):
        # Repeatedly fill both buffers, drain at full speed, then alternate ready.
        # The source stays valid throughout, including every backpressure cycle.
        phase = cycle % 96
        dut.oready.value = int(32 <= phase < 64 or (phase >= 64 and phase % 2 == 0))
        streams = await _step(dut, monitor, cycle)
        beat, valid, ready = streams["i"]
        assert valid == 1, f"cycle={cycle}: TVALID dropped during continuous traffic"
        assert beat == offered, f"cycle={cycle}: source changed the offered beat"
        if ready:
            stalled_cycles = 0
            consecutive_transfers += 1
            max_consecutive_transfers = max(max_consecutive_transfers, consecutive_transfers)
            if streams["o"][1:] == (1, 1):
                simultaneous_transfers += 1
            if scoreboard.input_handshakes == NUM_DATA_VALID_PAIRS:
                break
            # This edge accepted the current beat. Present the next full beat
            # immediately, without writing or deasserting TVALID between beats.
            index = scoreboard.input_handshakes
            offered = _beat((0x35 + index * 73) & 0xFF, index)
            _drive_beat(dut, offered)
        else:
            if stalled_cycles == 0:
                stall_episodes += 1
            stalled_cycles += 1
            max_stalled_cycles = max(max_stalled_cycles, stalled_cycles)
            consecutive_transfers = 0
            # Do not touch data, sidebands, or TVALID while waiting for TREADY.
    else:
        raise AssertionError(
            f"Continuous traffic timed out: {scoreboard.input_handshakes} accepted, "
            f"{scoreboard.output_handshakes} delivered"
        )

    # Only the final input handshake may end the continuously valid source.
    dut.ivalid.value = 0
    dut.oready.value = 1
    for drain_cycle in range(4):
        await _step(dut, monitor, cycle + 1 + drain_cycle)
    monitor.assert_drained(NUM_DATA_VALID_PAIRS)
    assert len(monitor.sidebands) == 64
    assert max_stalled_cycles >= 16, "Long input stalls were not exercised"
    assert max_consecutive_transfers >= 16, "Sustained consecutive transfers were not exercised"
    assert stall_episodes >= 2, "Repeated backpressure was not exercised"
    assert simultaneous_transfers > 0, "Simultaneous input/output transfers were not exercised"
    assert int(dut.cvalid.value) == int(dut.ovalid.value) == 0
    cocotb.log.info(
        "continuous-valid test passed: pairs=%d cycles=%d longest_stall=%d "
        "longest_transfer_run=%d",
        NUM_DATA_VALID_PAIRS,
        cycle + 1,
        max_stalled_cycles,
        max_consecutive_transfers,
    )


@cocotb.test()
async def test_input_output_buffer_sideband_corner_cases(dut) -> None:
    """Exhaust sidebands through bypass, storage, stalls and simultaneous transfers."""
    await _reset(dut)
    monitor = BufferMonitor()
    cycle = 0

    # Same data, different sidebands: data-only comparisons cannot pass this test.
    # All KEEP/DEST/LAST combinations must sustain one transfer per cycle.
    dut.oready.value = 1
    dut.ivalid.value = 1
    for index in range(64):
        _drive_beat(dut, _beat(0xA5, index))
        streams = await _step(dut, monitor, cycle)
        assert streams["i"][2] == streams["c"][2] == 1
        assert streams["o"][1] == int(index != 0), "Unexpected throughput bubble"
        cycle += 1
    dut.ivalid.value = 0
    await _step(dut, monitor, cycle)
    cycle += 1
    monitor.assert_drained(64)

    # Stall every sideband combination in both stages. KEEP=0, including LAST=1,
    # is still a beat and must not be dropped or have its data/sidebands altered.
    for index in range(64):
        data = 0xFF if index & 1 else 0
        dut.oready.value = 0
        dut.ivalid.value = 1
        _drive_beat(dut, _beat(data, index))
        streams = await _step(dut, monitor, cycle)
        assert streams["i"][2] == streams["c"][2] == 1
        assert streams["o"][1:] == (0, 0), "Output should initially be empty"
        cycle += 1

        _drive_beat(dut, _beat(data ^ 0xFF, index ^ 0x3F))
        streams = await _step(dut, monitor, cycle)
        assert streams["i"][2] == 1 and streams["c"][2] == 0
        cycle += 1

        # Both beats have been accepted. Invalid input payload may now change
        # freely, even while IREADY=0; it must not corrupt either stored beat.
        dut.ivalid.value = 0
        for stall in range(5):
            _drive_beat(dut, _beat(stall * 53, index + stall + 1))
            streams = await _step(dut, monitor, cycle)
            assert streams["i"][2] == streams["c"][2] == 0
            assert streams["c"][1] == streams["o"][1] == 1
            cycle += 1

        # Releasing backpressure drains the skid beat first. Hold the new beat
        # while IREADY=0, then replace the output beat on the following edge.
        dut.oready.value = 1
        dut.ivalid.value = 1
        _drive_beat(dut, _beat(data, index + 17))
        streams = await _step(dut, monitor, cycle)
        assert streams["i"][2] == 0
        assert streams["c"][1:] == streams["o"][1:] == (1, 1)
        cycle += 1
        streams = await _step(dut, monitor, cycle)
        assert all(streams[prefix][1:] == (1, 1) for prefix in ("i", "c", "o"))
        cycle += 1

        # Stop the sink again immediately after replacement. Noise while VALID=0
        # exercises the unbuffered core path without creating phantom transfers.
        dut.ivalid.value = 0
        dut.oready.value = 0
        for stall in range(4):
            _drive_beat(dut, _beat(0xFF ^ (stall * 53), index ^ stall))
            streams = await _step(dut, monitor, cycle)
            assert streams["c"][1] == 0 and streams["o"][1:] == (1, 0)
            cycle += 1
        dut.oready.value = 1
        await _step(dut, monitor, cycle)
        cycle += 1
        streams = await _step(dut, monitor, cycle)
        assert streams["c"][1] == streams["o"][1] == 0
        cycle += 1
        monitor.assert_drained(64 + (index + 1) * 3)

    assert len(monitor.sidebands) == 64


@cocotb.test()
async def test_input_output_buffer_reset_flushes_pending(dut) -> None:
    """Asynchronous reset flushes empty, output-only and fully occupied buffers."""
    await _reset(dut)
    for pending in (0, 1, 2):
        monitor = BufferMonitor()
        cycle = 0
        dut.oready.value = 0
        for index in range(pending):
            dut.ivalid.value = 1
            _drive_beat(dut, AxisBeat(0xFF - index, 1, 0xF - index, 1))
            streams = await _step(dut, monitor, cycle)
            assert streams["i"][2] == 1
            cycle += 1
        assert len(monitor.scoreboard.expected) == pending

        # Assert reset between edges, while the sink is stalled. No rising edge
        # is allowed before checking that VALID and output sidebands clear.
        dut.ivalid.value = 0
        _drive_beat(dut, AxisBeat(0, 0, 0, 0))
        await Timer(2, unit="ns")
        dut.rst_n.value = 0
        await Timer(1, unit="ps")
        await ReadOnly()
        _check_reset(dut)
        await RisingEdge(dut.clk)
        await ReadOnly()
        _check_reset(dut)
        await Timer(1, unit="ps")
        dut.rst_n.value = 1

        # Pending expectations were intentionally flushed. Check for stale VALID
        # with the sink ready before accepting a distinct post-reset beat.
        monitor = BufferMonitor()
        dut.oready.value = 1
        for index in range(3):
            _drive_beat(dut, _beat(index * 127, index + pending))
            streams = await _step(dut, monitor, cycle)
            assert streams["c"][1] == streams["o"][1] == 0
            cycle += 1
        monitor.assert_drained(0)

        dut.ivalid.value = 1
        _drive_beat(dut, AxisBeat(0x35 + pending, pending & 1, pending, 0))
        await _step(dut, monitor, cycle)
        cycle += 1
        dut.ivalid.value = 0
        for _ in range(3):
            await _step(dut, monitor, cycle)
            cycle += 1
        monitor.assert_drained(1)