"""usb_rx_filter: routing, dropping and the buffer-size limit of OUT/SETUP payloads.

The route on ``rx_route`` (accept, setup, entry) in the cycle a packet's first
beat is taken on ``rx_payload`` applies to the whole packet; later changes do not
matter. A packet that is not accepted is consumed and nothing comes out. An
accepted packet comes out on ``routed`` beat for beat with the same data and
last, ``dest = setup & entry`` (bit 4 is the SETUP flag), and keep cleared for
bytes past the entry's buffer: only the first 2 ** (size + 3) kept bytes keep
their keep. ``rx_payload`` tdest is not used (the parser drives 0).

Both streams follow AXI4-Stream: a beat offered on ``routed`` stays unchanged
until it is taken. ``SEED`` replays a run.
"""
from __future__ import annotations

import os
import random
from collections import deque
from dataclasses import dataclass
from fractions import Fraction

import cocotb
from cocotb.triggers import ReadOnly, ReadWrite, RisingEdge

from drivers.axis_utils import THROTTLE_MODES, Throttle, beats_differ, resolved
from drivers.clock_reset import ClockDomain, start_clocks_and_reset
from models.usb_ram_adapter_model import StreamBeat

ENTRIES = 16
DEST_WIDTH = 4


@dataclass(frozen=True)
class Route:
    accept: int
    setup: int
    entry: int


def _seed() -> int:
    value = os.environ.get("SEED")
    return int(value, 0) if value is not None else random.SystemRandom().getrandbits(32)


def packet_beats(payload: bytes) -> list[StreamBeat]:
    if not payload:
        return [StreamBeat(None, 0, 0, 1)]
    return [StreamBeat(byte, 1, 0, int(index == len(payload) - 1)) for index, byte in enumerate(payload)]


def expected_routed(route: Route, payload: bytes, size: int) -> list[StreamBeat] | None:
    """What ``routed`` carries for one input packet, or None if it is dropped."""
    if not route.accept:
        return None
    dest = route.setup << DEST_WIDTH | route.entry
    if not payload:
        return [StreamBeat(None, 0, dest, 1)]
    limit = 8 << size
    return [StreamBeat(byte if index < limit else None, int(index < limit), dest, int(index == len(payload) - 1))
            for index, byte in enumerate(payload)]


class FilterEnv:
    def __init__(self, dut, rng: random.Random, sizes: list[int], in_mode: str, out_mode: str) -> None:
        self.dut = dut
        self.rng = rng
        self.sizes = sizes
        self.in_throttle = Throttle(rng, in_mode)
        self.out_throttle = Throttle(rng, out_mode)
        self.queue: deque[tuple[StreamBeat, Route, bool]] = deque()
        self.in_flight = False
        self.expected: deque[list[StreamBeat]] = deque()
        self.current: list[StreamBeat] = []
        self.errors: list[str] = []

    async def start(self) -> None:
        dut = self.dut
        dut.endpoint_size.value = sum(size << (3 * i) for i, size in enumerate(self.sizes))
        for name in ("rx_route_entry", "rx_route_accept", "rx_route_setup", "rx_payload_tdata",
                     "rx_payload_tkeep", "rx_payload_tlast", "rx_payload_tvalid", "routed_tready"):
            getattr(dut, name).value = 0
        await start_clocks_and_reset(ClockDomain(dut.clk, dut.rst_n, Fraction(20833, 1000)))
        cocotb.start_soon(self._driver())
        cocotb.start_soon(self._monitor())

    def send(self, route: Route, payload: bytes) -> None:
        beats = packet_beats(payload)
        expected = expected_routed(route, payload, self.sizes[route.entry])
        if expected is not None:
            self.expected.append(expected)
        self.queue.extend((beat, route, index == 0) for index, beat in enumerate(beats))

    async def _driver(self) -> None:
        dut = self.dut
        item: tuple[StreamBeat, Route, bool] | None = None
        while True:
            await RisingEdge(dut.clk)
            await ReadWrite()
            if item is None and self.queue and self.in_throttle():
                item = self.queue.popleft()
                self.in_flight = True
            if item is None:
                dut.rx_payload_tvalid.value = 0
                dut.rx_payload_tdata.value = self.rng.randrange(256)
                dut.rx_payload_tkeep.value = self.rng.randrange(2)
                dut.rx_payload_tlast.value = self.rng.randrange(2)
            else:
                beat, route, first = item
                dut.rx_payload_tvalid.value = 1
                dut.rx_payload_tdata.value = beat.data if beat.data is not None else self.rng.randrange(256)
                dut.rx_payload_tkeep.value = beat.keep
                dut.rx_payload_tlast.value = beat.last
            # The route counts only in the cycle the first beat is taken.
            if item is not None and item[2]:
                route = item[1]
                dut.rx_route_accept.value = route.accept
                dut.rx_route_setup.value = route.setup
                dut.rx_route_entry.value = route.entry
            else:
                dut.rx_route_accept.value = self.rng.randrange(2)
                dut.rx_route_setup.value = self.rng.randrange(2)
                dut.rx_route_entry.value = self.rng.randrange(ENTRIES)
            await ReadOnly()
            try:
                ready = resolved(dut.rx_payload_tready.value, "rx_payload_tready")
            except ValueError as error:
                self.errors.append(str(error))
                ready = 0
            if item is not None and ready:
                item = None
                self.in_flight = bool(self.queue)

    async def _monitor(self) -> None:
        dut = self.dut
        stalled: StreamBeat | None = None
        while True:
            await RisingEdge(dut.clk)
            await ReadWrite()
            ready = int(self.out_throttle())
            dut.routed_tready.value = ready
            await ReadOnly()
            try:
                valid = resolved(dut.routed_tvalid.value, "routed_tvalid")
                if not valid:
                    if stalled is not None:
                        self.errors.append(f"routed beat {stalled} withdrawn while stalled")
                    stalled = None
                    continue
                keep = resolved(dut.routed_tkeep.value, "routed_tkeep")
                beat = StreamBeat(resolved(dut.routed_tdata.value, "routed_tdata") if keep else None, keep,
                                  resolved(dut.routed_tdest.value, "routed_tdest"),
                                  resolved(dut.routed_tlast.value, "routed_tlast"))
            except ValueError as error:
                self.errors.append(str(error))
                continue
            if stalled is not None and beats_differ(stalled, beat):
                self.errors.append(f"routed beat changed while stalled: {stalled} -> {beat}")
            if not ready:
                stalled = beat
                continue
            stalled = None
            self.current.append(beat)
            if beat.last:
                self._check_packet(self.current)
                self.current = []

    def _check_packet(self, packet: list[StreamBeat]) -> None:
        if not self.expected:
            self.errors.append(f"unexpected packet on routed: {packet}")
            return
        expected = self.expected.popleft()
        if packet != expected:
            self.errors.append(f"routed packet {packet}, expected {expected}")

    async def drain(self, context: str) -> None:
        for _ in range(20000):
            await RisingEdge(self.dut.clk)
            if not self.queue and not self.in_flight and not self.expected and not self.current:
                break
        for _ in range(16):
            await RisingEdge(self.dut.clk)
        if self.queue or self.expected or self.current:
            self.errors.append(f"{context}: {len(self.queue)} input beats and {len(self.expected)} packets left")
        assert not self.errors, f"{context}:\n" + "\n".join(self.errors[:20])


def _payload(rng: random.Random, limit: int) -> bytes:
    length = rng.choice((0, 1, 7, 8, 9, rng.randrange(limit + 1), limit, limit + 1, limit + rng.randrange(1, 9)))
    return bytes(rng.randrange(256) for _ in range(length))


@cocotb.test()
async def test_size_limit_every_size(dut) -> None:
    """Each size gets packets just inside, at and past its buffer."""
    seed = _seed()
    dut._log.info("SEED=%d", seed)
    rng = random.Random(seed)
    sizes = [index % 8 for index in range(ENTRIES)]
    env = FilterEnv(dut, rng, sizes, "always", "always")
    await env.start()
    for entry, size in enumerate(sizes):
        limit = 8 << size
        for length in (0, 1, limit - 1, limit, limit + 1, limit + 3):
            env.send(Route(1, 0, entry), bytes(rng.randrange(256) for _ in range(length)))
    await env.drain(f"seed={seed}")


@cocotb.test()
async def test_drop_and_route(dut) -> None:
    """Not accepted packets disappear; accepted ones carry setup & entry in dest."""
    seed = _seed()
    dut._log.info("SEED=%d", seed)
    rng = random.Random(seed)
    sizes = [7] * ENTRIES
    env = FilterEnv(dut, rng, sizes, "always", "always")
    await env.start()
    for accept in (0, 1):
        for setup in (0, 1):
            for entry in (0, 5, 15):
                env.send(Route(accept, setup, entry), bytes(range(8)))
                env.send(Route(accept, setup, entry), b"")
    await env.drain(f"seed={seed}")


@cocotb.test()
async def test_random(dut) -> None:
    """Random routes, sizes, lengths and back-pressure on both sides."""
    seed = _seed()
    dut._log.info("SEED=%d", seed)
    rng = random.Random(seed)
    sizes = [rng.randrange(8) for _ in range(ENTRIES)]
    env = FilterEnv(dut, rng, sizes, "always", "always")
    await env.start()
    for in_mode in THROTTLE_MODES:
        for out_mode in THROTTLE_MODES:
            env.in_throttle = Throttle(rng, in_mode)
            env.out_throttle = Throttle(rng, out_mode)
            for _ in range(12):
                route = Route(int(rng.random() < 0.75), rng.randrange(2), rng.randrange(ENTRIES))
                env.send(route, _payload(rng, min(8 << sizes[route.entry], 80)))
            await env.drain(f"seed={seed} in={in_mode} out={out_mode}")
