"""Black-box verification of the clock-domain-crossing primitives.

The harness (``cdc_tb.vhd``) holds one ``cdc_pulse`` and one ``cdc_handshake``
(16-bit data), both crossing from clock domain ``a`` to clock domain ``b``.
Every test runs at several clock ratios: ``a`` faster, slower, much faster,
much slower, and equal to ``b``.

Specification checked:

* ``cdc_pulse``: every one-cycle ``src_pulse`` gives exactly one one-cycle
  ``dst_pulse``, within ``CDC_STAGES + 2`` destination cycles plus one source
  cycle, as long as source pulses are spaced by at least that time.
* ``cdc_handshake``: an item is taken when ``src_valid`` and ``src_ready`` are
  both high on a source edge. Each item appears once, in order, on ``dst_data``
  with a one-cycle ``dst_valid``; ``dst_data`` holds its value until the next
  ``dst_valid``. ``src_ready`` is never high again before the previous item
  has been delivered, and every item is delivered within a bounded time.
* After reset ``dst_pulse`` and ``dst_valid`` are low and ``src_ready`` is high.

``SEED`` replays a run; otherwise a fresh seed is drawn and printed with any failure.
"""
from __future__ import annotations

import math
import os
import random
from collections import deque
from fractions import Fraction

import cocotb
from cocotb.triggers import ReadOnly, ReadWrite, RisingEdge, Timer
from cocotb.utils import get_sim_time

from drivers.clock_reset import ClockDomain, start_clocks_and_reset

CDC_STAGES = 2
CLOCK_PAIRS = (
    (Fraction(16667, 1000), Fraction(20833, 1000)),
    (Fraction(20833, 1000), Fraction(16667, 1000)),
    (Fraction(10), Fraction(47)),
    (Fraction(47), Fraction(10)),
    (Fraction(20), Fraction(20)),
)
CLOCK_PAIR_NAMES = ("a60_b48", "a48_b60", "a100_b21", "a21_b100", "equal")
ITEMS = 200


def _seed_from_environment() -> int:
    value = os.environ.get("SEED")
    return int(value, 0) if value is not None else random.SystemRandom().getrandbits(32)


def _now() -> float:
    return get_sim_time(unit="ns")


def _bit(signal, name: str, errors: list[str]) -> int:
    value = signal.value
    if not value.is_resolvable:
        errors.append(f"{name} is unresolved: {value} at {_now():.1f} ns")
        return 0
    return int(value)


class CdcEnv:
    def __init__(self, dut, name: str, periods: tuple[Fraction, Fraction]) -> None:
        self.dut = dut
        self.period_a, self.period_b = periods
        self.seed = _seed_from_environment()
        self.rng = random.Random(self.seed)
        self.name = f"{name} (Ta={float(self.period_a):.3f} ns, Tb={float(self.period_b):.3f} ns)"
        self.errors: list[str] = []

    async def start(self) -> None:
        dut = self.dut
        dut.pulse_src.value = 0
        dut.hs_src_valid.value = 0
        dut.hs_src_data.value = 0
        await start_clocks_and_reset(
            ClockDomain(dut.clk_a, dut.rst_a_n, self.period_a),
            ClockDomain(dut.clk_b, dut.rst_b_n, self.period_b),
        )

    async def a_cycle(self) -> None:
        await RisingEdge(self.dut.clk_a)
        await ReadWrite()

    def finish(self) -> None:
        if self.errors:
            listing = "\n  ".join(self.errors[:40])
            more = f"\n  ... and {len(self.errors) - 40} more" if len(self.errors) > 40 else ""
            raise AssertionError(
                f"{self.name}: {len(self.errors)} error(s), SEED={self.seed:#x}\n  {listing}{more}"
            )
        self.dut._log.info("%s passed, SEED=%#x", self.name, self.seed)


def _pulse_latency_bound(env: CdcEnv) -> float:
    return float((CDC_STAGES + 2) * env.period_b + env.period_a)


def _handshake_latency_bound(env: CdcEnv) -> float:
    return float((CDC_STAGES + 3) * env.period_b + 2 * env.period_a)


@cocotb.test()
@cocotb.parametrize(clocks=list(zip(CLOCK_PAIR_NAMES, CLOCK_PAIRS)))
async def test_reset_state(dut, clocks) -> None:
    """Outputs are idle after reset and stay idle without input."""
    env = CdcEnv(dut, f"reset state {clocks[0]}", clocks[1])
    await env.start()
    for _ in range(64):
        await RisingEdge(dut.clk_b)
        await ReadOnly()
        if _bit(dut.pulse_dst, "pulse_dst", env.errors):
            env.errors.append(f"pulse_dst high without a source pulse at {_now():.1f} ns")
        if _bit(dut.hs_dst_valid, "hs_dst_valid", env.errors):
            env.errors.append(f"hs_dst_valid high without a source item at {_now():.1f} ns")
        if not _bit(dut.hs_src_ready, "hs_src_ready", env.errors):
            env.errors.append(f"hs_src_ready low while idle at {_now():.1f} ns")
        if len(env.errors) > 8:
            break
    env.finish()


@cocotb.test()
@cocotb.parametrize(clocks=list(zip(CLOCK_PAIR_NAMES, CLOCK_PAIRS)))
async def test_pulse(dut, clocks) -> None:
    """Every spaced source pulse gives exactly one one-cycle destination pulse."""
    env = CdcEnv(dut, f"cdc_pulse {clocks[0]}", clocks[1])
    await env.start()
    bound = _pulse_latency_bound(env)
    spacing = math.ceil(bound / float(env.period_a)) + 2
    sent: deque[float] = deque()
    received = 0
    done = False

    async def monitor() -> None:
        nonlocal received
        previous = 0
        while not done:
            await RisingEdge(dut.clk_b)
            await ReadOnly()
            pulse = _bit(dut.pulse_dst, "pulse_dst", env.errors)
            if pulse and previous:
                env.errors.append(f"pulse_dst high for more than one cycle at {_now():.1f} ns")
            if pulse and not previous:
                received += 1
                if not sent:
                    env.errors.append(f"pulse_dst without a source pulse at {_now():.1f} ns")
                else:
                    latency = _now() - sent.popleft()
                    if latency > bound:
                        env.errors.append(
                            f"pulse latency {latency:.1f} ns exceeds {bound:.1f} ns at {_now():.1f} ns")
            previous = pulse

    monitor_task = cocotb.start_soon(monitor())
    for _ in range(ITEMS):
        await env.a_cycle()
        dut.pulse_src.value = 1
        sent.append(_now())
        await env.a_cycle()
        dut.pulse_src.value = 0
        for _ in range(spacing + env.rng.choice((0, 0, env.rng.randint(0, spacing)))):
            await env.a_cycle()
    await Timer(2 * bound, unit="ns")
    done = True
    await RisingEdge(dut.clk_b)
    if received != ITEMS:
        env.errors.append(f"{ITEMS} source pulses gave {received} destination pulses")
    monitor_task.cancel()
    env.finish()


@cocotb.test()
@cocotb.parametrize(
    clocks=list(zip(CLOCK_PAIR_NAMES, CLOCK_PAIRS)),
    mode=["always", "random", "sparse"],
)
async def test_handshake(dut, clocks, mode) -> None:
    """Items cross once, in order, with backpressure, held data and bounded latency."""
    env = CdcEnv(dut, f"cdc_handshake {clocks[0]} {mode}", clocks[1])
    await env.start()
    bound = _handshake_latency_bound(env)
    items = [env.rng.getrandbits(16) for _ in range(ITEMS)]
    in_flight: deque[tuple[int, float]] = deque()
    delivered = 0
    last_delivery_time = -1.0
    done = False

    def offer() -> bool:
        if mode == "always":
            return True
        if mode == "random":
            return env.rng.random() < 0.5
        return env.rng.random() < 0.1

    async def monitor() -> None:
        nonlocal delivered, last_delivery_time
        previous_valid = 0
        held: int | None = None
        while not done:
            await RisingEdge(dut.clk_b)
            await ReadOnly()
            valid = _bit(dut.hs_dst_valid, "hs_dst_valid", env.errors)
            if valid and previous_valid:
                env.errors.append(f"hs_dst_valid high for more than one cycle at {_now():.1f} ns")
            previous_valid = valid
            if not valid:
                if held is not None:
                    data = dut.hs_dst_data.value
                    if not data.is_resolvable or int(data) != held:
                        env.errors.append(
                            f"hs_dst_data changed from {held:#06x} to {data} between items at {_now():.1f} ns")
                        held = None
                continue
            data = _bit(dut.hs_dst_data, "hs_dst_data", env.errors)
            held = data
            if not in_flight:
                env.errors.append(f"hs_dst_valid without an accepted item at {_now():.1f} ns")
                continue
            expected, accepted_at = in_flight.popleft()
            if data != expected:
                env.errors.append(
                    f"item {delivered}: expected {expected:#06x}, observed {data:#06x} at {_now():.1f} ns")
            latency = _now() - accepted_at
            if latency > bound:
                env.errors.append(
                    f"item {delivered}: latency {latency:.1f} ns exceeds {bound:.1f} ns")
            delivered += 1
            last_delivery_time = _now()

    monitor_task = cocotb.start_soon(monitor())
    index = 0
    idle_cycles = 0
    timeout = math.ceil(4 * bound / float(env.period_a)) + 16
    while index < len(items):
        await env.a_cycle()
        valid = offer() or bool(int(dut.hs_src_valid.value))
        dut.hs_src_valid.value = int(valid)
        dut.hs_src_data.value = items[index] if valid else env.rng.getrandbits(16)
        await ReadOnly()
        ready = _bit(dut.hs_src_ready, "hs_src_ready", env.errors)
        if ready:
            idle_cycles = 0
        if valid and ready:
            if in_flight:
                env.errors.append(
                    f"item {index} accepted at {_now():.1f} ns before item {index - 1} was delivered")
            in_flight.append((items[index], _now()))
            index += 1
            await env.a_cycle()
            dut.hs_src_valid.value = 0
        elif not ready:
            idle_cycles += 1
            if idle_cycles > timeout:
                env.errors.append(f"hs_src_ready stayed low for {idle_cycles} cycles before item {index}")
                break
    await Timer(2 * bound, unit="ns")
    done = True
    await RisingEdge(dut.clk_b)
    if delivered != index:
        env.errors.append(f"{index} accepted items gave {delivered} deliveries")
    monitor_task.cancel()
    env.finish()
