"""The initial clock/reset sequence shared by every RTL test.

Every test starts the same way, in every clock domain (``_sys`` and ``_usb``
alike): reset is driven 1 -> 0 *before* the clock starts, so no clock edge can
reach a register that has not been reset. Reset is then released
synchronously, right after a rising edge of the domain's own clock.

Resets later in a test (e.g. an asynchronous reset in the middle of traffic)
are test-specific and do not use this module.
"""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ReadWrite, RisingEdge, Timer

RESET_HOLD_NS = 100


@dataclass(frozen=True)
class ClockDomain:
    clk: object
    rst_n: object
    period_ns: Fraction | int | float


async def start_clocks_in_reset(*domains: ClockDomain) -> list:
    """Toggle every reset 1 -> 0, then start the clocks. Returns the clock tasks."""
    for domain in domains:
        domain.rst_n.value = 1
    await Timer(1, unit="ns")
    for domain in domains:
        domain.rst_n.value = 0
    return [
        cocotb.start_soon(Clock(domain.clk, domain.period_ns, unit="ns").start(start_high=False))
        for domain in domains
    ]


async def release_resets(*domains: ClockDomain) -> None:
    """Release each reset right after a rising edge of its own clock."""
    for domain in domains:
        await RisingEdge(domain.clk)
        await ReadWrite()
        domain.rst_n.value = 1


async def start_clocks_and_reset(*domains: ClockDomain, hold_ns: float = RESET_HOLD_NS) -> list:
    """The full initial sequence: reset 1 -> 0, clocks start, hold, synchronous release."""
    clocks = await start_clocks_in_reset(*domains)
    await Timer(hold_ns, unit="ns")
    await release_resets(*domains)
    return clocks
