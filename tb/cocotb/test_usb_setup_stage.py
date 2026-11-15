"""usb_setup_stage: SETUP data is written only after usb_protocol accepts it.

Input ``routed`` carries ``dest = setup & entry`` (bit 4 is the SETUP flag).

* A packet without the flag passes to ``write`` unchanged, with ``dest = entry``.
* A SETUP packet is consumed and nothing comes out while it arrives; its first 8
  kept bytes are held (bytes past 8 are dropped).
* ``setup_received`` accepts the most recent SETUP. It may come before that
  packet's last beat is taken, but not before its first. A SETUP that is never
  accepted writes nothing, and the next SETUP replaces it.
* An accepted SETUP comes out as its own 8-beat packet (keep = 1, ``dest`` = its
  entry, last on the 8th), between packets, never inside one.
  ``setup_writing`` is high while those beats are offered, with ``setup_entry``
  set to the entry. ``setup_written`` pulses once, in the cycle after the 8th
  beat is taken, with ``setup_entry`` still set.

Both streams follow AXI4-Stream: a beat offered on ``write`` stays unchanged
until it is taken. ``SEED`` replays a run.
"""
from __future__ import annotations

import os
import random
from collections import deque
from fractions import Fraction

import cocotb
from cocotb.triggers import ReadOnly, ReadWrite, RisingEdge

from drivers.axis_utils import THROTTLE_MODES, Throttle, beats_differ, resolved
from drivers.clock_reset import ClockDomain, start_clocks_and_reset
from models.usb_ram_adapter_model import StreamBeat

ENTRIES = 16
DEST_WIDTH = 4
SETUP_FLAG = 1 << DEST_WIDTH


def _seed() -> int:
    value = os.environ.get("SEED")
    return int(value, 0) if value is not None else random.SystemRandom().getrandbits(32)


def packet_beats(dest: int, payload: bytes) -> list[StreamBeat]:
    if not payload:
        return [StreamBeat(None, 0, dest, 1)]
    return [StreamBeat(byte, 1, dest, int(index == len(payload) - 1)) for index, byte in enumerate(payload)]


class StageEnv:
    def __init__(self, dut, rng: random.Random) -> None:
        self.dut = dut
        self.rng = rng
        self.in_throttle = Throttle(rng, "always")
        self.out_throttle = Throttle(rng, "always")
        self.queue: deque[tuple[StreamBeat, int]] = deque()
        self.in_flight = False
        self.taken_first: set[int] = set()
        self.packet_id = 0
        self.cycle = 0
        self.expected_live: deque[list[StreamBeat]] = deque()
        self.expected_setups: deque[list[StreamBeat]] = deque()
        self.live: list[StreamBeat] = []
        self.replay: list[StreamBeat] = []
        self.written_due: tuple[int, int] | None = None
        self.written_count = 0
        self.replays_done = 0
        self.errors: list[str] = []

    async def start(self) -> None:
        dut = self.dut
        for name in ("routed_tdata", "routed_tkeep", "routed_tdest", "routed_tlast", "routed_tvalid",
                     "setup_received", "write_tready"):
            getattr(dut, name).value = 0
        await start_clocks_and_reset(ClockDomain(dut.clk, dut.rst_n, Fraction(20833, 1000)))
        cocotb.start_soon(self._driver())
        cocotb.start_soon(self._monitor())

    # ------------------------------------------------------------ stimulus
    def send(self, dest: int, payload: bytes) -> int:
        self.packet_id += 1
        self.queue.extend((beat, self.packet_id if index == 0 else 0)
                          for index, beat in enumerate(packet_beats(dest, payload)))
        return self.packet_id

    def live_packet(self, entry: int, payload: bytes) -> None:
        self.send(entry, payload)
        self.expected_live.append(packet_beats(entry, payload))

    async def setup_packet(self, entry: int, payload: bytes, accept: bool, delay: int) -> None:
        """Send a SETUP packet; ``delay`` cycles after its first beat is taken,
        pulse setup_received if it is accepted."""
        await self.accept_after(self.send(SETUP_FLAG | entry, payload), entry, payload, accept, delay)

    async def accept_after(self, packet: int, entry: int, payload: bytes, accept: bool, delay: int) -> None:
        while packet not in self.taken_first:
            await RisingEdge(self.dut.clk)
        for _ in range(delay):
            await RisingEdge(self.dut.clk)
        if not accept:
            return
        kept = payload[:8]
        self.expected_setups.append([StreamBeat(byte, 1, entry, int(index == 7)) for index, byte in enumerate(kept)])
        await RisingEdge(self.dut.clk)
        await ReadWrite()
        self.dut.setup_received.value = 1
        await RisingEdge(self.dut.clk)
        await ReadWrite()
        self.dut.setup_received.value = 0

    async def _driver(self) -> None:
        dut = self.dut
        item: tuple[StreamBeat, int] | None = None
        while True:
            await RisingEdge(dut.clk)
            await ReadWrite()
            if item is None and self.queue and self.in_throttle():
                item = self.queue.popleft()
                self.in_flight = True
            if item is None:
                dut.routed_tvalid.value = 0
                dut.routed_tdata.value = self.rng.randrange(256)
                dut.routed_tkeep.value = self.rng.randrange(2)
                dut.routed_tdest.value = self.rng.randrange(2 * ENTRIES)
                dut.routed_tlast.value = self.rng.randrange(2)
            else:
                beat = item[0]
                dut.routed_tvalid.value = 1
                dut.routed_tdata.value = beat.data if beat.data is not None else self.rng.randrange(256)
                dut.routed_tkeep.value = beat.keep
                dut.routed_tdest.value = beat.dest
                dut.routed_tlast.value = beat.last
            await ReadOnly()
            try:
                ready = resolved(dut.routed_tready.value, "routed_tready")
            except ValueError as error:
                self.errors.append(str(error))
                ready = 0
            if item is not None and ready:
                if item[1]:
                    self.taken_first.add(item[1])
                item = None
                self.in_flight = bool(self.queue)

    # ------------------------------------------------------------- checking
    async def _monitor(self) -> None:
        dut = self.dut
        stalled: StreamBeat | None = None
        while True:
            await RisingEdge(dut.clk)
            await ReadWrite()
            ready = int(self.out_throttle())
            dut.write_tready.value = ready
            await ReadOnly()
            self.cycle += 1
            try:
                written = resolved(dut.setup_written.value, "setup_written")
                writing = resolved(dut.setup_writing.value, "setup_writing")
                entry = resolved(dut.setup_entry.value, "setup_entry")
                valid = resolved(dut.write_tvalid.value, "write_tvalid")
            except ValueError as error:
                self.errors.append(str(error))
                continue
            self._check_written(written, entry)
            if not valid:
                if stalled is not None:
                    self.errors.append(f"write beat {stalled} withdrawn while stalled")
                stalled = None
                continue
            try:
                keep = resolved(dut.write_tkeep.value, "write_tkeep")
                beat = StreamBeat(resolved(dut.write_tdata.value, "write_tdata") if keep else None, keep,
                                  resolved(dut.write_tdest.value, "write_tdest"),
                                  resolved(dut.write_tlast.value, "write_tlast"))
            except ValueError as error:
                self.errors.append(str(error))
                continue
            if stalled is not None and beats_differ(stalled, beat):
                self.errors.append(f"write beat changed while stalled: {stalled} -> {beat}")
            if writing and entry != beat.dest:
                self.errors.append(f"setup_entry={entry} while a SETUP beat for {beat.dest} is offered")
            if not ready:
                stalled = beat
                continue
            stalled = None
            if writing:
                if self.live:
                    self.errors.append(f"SETUP beat {beat} inside a packet: {self.live}")
                self.replay.append(beat)
                if beat.last:
                    self._check_replay(self.replay)
                    self.replay = []
                    self.written_due = (self.cycle + 1, beat.dest)
            else:
                if self.replay:
                    self.errors.append(f"packet beat {beat} inside a SETUP write: {self.replay}")
                self.live.append(beat)
                if beat.last:
                    self._check_live(self.live)
                    self.live = []

    def _check_written(self, written: int, entry: int) -> None:
        due = self.written_due
        if due is not None and self.cycle == due[0]:
            self.written_due = None
            if not written:
                self.errors.append(f"cycle {self.cycle}: no setup_written after the SETUP write")
            elif entry != due[1]:
                self.errors.append(f"setup_written with setup_entry={entry}, expected {due[1]}")
            if written:
                self.written_count += 1
        elif written:
            self.errors.append(f"cycle {self.cycle}: setup_written without a finished SETUP write")

    def _check_live(self, packet: list[StreamBeat]) -> None:
        if not self.expected_live:
            self.errors.append(f"unexpected packet on write: {packet}")
            return
        expected = self.expected_live.popleft()
        if packet != expected:
            self.errors.append(f"packet {packet}, expected {expected}")

    def _check_replay(self, packet: list[StreamBeat]) -> None:
        self.replays_done += 1
        if not self.expected_setups:
            self.errors.append(f"SETUP write without an accepted SETUP: {packet}")
            return
        expected = self.expected_setups.popleft()
        if packet != expected:
            self.errors.append(f"SETUP write {packet}, expected {expected}")

    async def drain(self, context: str) -> None:
        for _ in range(20000):
            await RisingEdge(self.dut.clk)
            if (not self.queue and not self.in_flight and not self.expected_live and not self.expected_setups
                    and not self.live and not self.replay and self.written_due is None):
                break
        for _ in range(32):
            await RisingEdge(self.dut.clk)
        await ReadOnly()
        if int(self.dut.setup_writing.value):
            self.errors.append(f"{context}: setup_writing still high when idle")
        if self.queue or self.expected_live or self.expected_setups:
            self.errors.append(f"{context}: {len(self.queue)} input beats, {len(self.expected_live)} packets and "
                               f"{len(self.expected_setups)} SETUP writes left")
        if self.written_count != self.replays_done:
            self.errors.append(f"{context}: {self.replays_done} SETUP writes, {self.written_count} setup_written")
        assert not self.errors, f"{context}:\n" + "\n".join(self.errors[:20])
        await RisingEdge(self.dut.clk)


def _bytes(rng: random.Random, length: int) -> bytes:
    return bytes(rng.randrange(256) for _ in range(length))


async def _env(dut) -> tuple[StageEnv, random.Random, int]:
    seed = _seed()
    dut._log.info("SEED=%d", seed)
    rng = random.Random(seed)
    env = StageEnv(dut, rng)
    await env.start()
    return env, rng, seed


@cocotb.test()
async def test_live_packets_pass(dut) -> None:
    env, rng, seed = await _env(dut)
    for entry in range(ENTRIES):
        env.live_packet(entry, _bytes(rng, rng.choice((0, 1, 8, 64))))
    await env.drain(f"seed={seed}")


@cocotb.test()
async def test_setup_accepted(dut) -> None:
    env, rng, seed = await _env(dut)
    for entry in (0, 3, 15):
        await env.setup_packet(entry, _bytes(rng, 8), accept=True, delay=12)
        await env.drain(f"seed={seed} entry={entry}")


@cocotb.test()
async def test_setup_rejected(dut) -> None:
    """A SETUP that is never accepted writes nothing; the next one writes its own data."""
    env, rng, seed = await _env(dut)
    await env.setup_packet(0, _bytes(rng, 8), accept=False, delay=12)
    await env.drain(f"seed={seed} rejected")
    env.live_packet(1, _bytes(rng, 5))
    await env.setup_packet(0, _bytes(rng, 7), accept=False, delay=12)
    await env.setup_packet(0, _bytes(rng, 8), accept=True, delay=12)
    await env.drain(f"seed={seed} accepted after rejected")


@cocotb.test()
async def test_setup_cap(dut) -> None:
    """Only the first 8 bytes of a SETUP are held."""
    env, rng, seed = await _env(dut)
    await env.setup_packet(2, _bytes(rng, 12), accept=True, delay=20)
    await env.drain(f"seed={seed}")
    await env.setup_packet(2, _bytes(rng, 64), accept=True, delay=70)
    await env.drain(f"seed={seed}")


@cocotb.test()
async def test_setup_received_early(dut) -> None:
    """setup_received before the last SETUP beat: the write waits for it."""
    env, rng, seed = await _env(dut)
    env.in_throttle = Throttle(rng, "sparse")
    for delay in (0, 1, 3):
        await env.setup_packet(4, _bytes(rng, 8), accept=True, delay=delay)
        await env.drain(f"seed={seed} delay={delay}")


@cocotb.test()
async def test_setup_between_packets(dut) -> None:
    """A packet right behind the SETUP: the SETUP write goes between packets."""
    env, rng, seed = await _env(dut)
    for delay in range(0, 24, 2):
        env.out_throttle = Throttle(rng, "random")
        payload = _bytes(rng, 8)
        packet = env.send(SETUP_FLAG, payload)
        setup = cocotb.start_soon(env.accept_after(packet, 0, payload, True, delay))
        env.live_packet(0, _bytes(rng, rng.choice((0, 3, 16))))
        env.live_packet(1, _bytes(rng, 40))
        await setup
        await env.drain(f"seed={seed} delay={delay}")


@cocotb.test()
async def test_setup_back_to_back(dut) -> None:
    """The next SETUP arrives while the previous one is being written and is
    accepted at once: it is written with its own data, after its last beat."""
    env, rng, seed = await _env(dut)
    for out_mode in ("sparse", "random", "always"):
        env.out_throttle = Throttle(rng, out_mode)
        for _ in range(4):
            first, second = _bytes(rng, 8), _bytes(rng, 8)
            await env.setup_packet(1, first, accept=True, delay=8)
            await env.setup_packet(2, second, accept=True, delay=0)
            await env.drain(f"seed={seed} out={out_mode}")


@cocotb.test()
async def test_random(dut) -> None:
    env, rng, seed = await _env(dut)
    for in_mode in THROTTLE_MODES:
        for out_mode in THROTTLE_MODES:
            env.in_throttle = Throttle(rng, in_mode)
            env.out_throttle = Throttle(rng, out_mode)
            for _ in range(10):
                if rng.random() < 0.4:
                    length = rng.choice((8, 8, 8, 0, 7, 12))
                    await env.setup_packet(rng.randrange(ENTRIES), _bytes(rng, length),
                                           accept=length >= 8 and rng.random() < 0.8, delay=rng.randrange(40))
                else:
                    env.live_packet(rng.randrange(ENTRIES), _bytes(rng, rng.choice((0, 1, 8, rng.randrange(70)))))
            await env.drain(f"seed={seed} in={in_mode} out={out_mode}")
