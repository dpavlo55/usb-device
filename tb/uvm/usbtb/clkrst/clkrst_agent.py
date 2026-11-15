"""Clock and reset UVC.

Each clock domain gets one agent. Its driver turns :class:`ClkRstItem` operations into a
running clock and a reset level; its monitor watches the reset and publishes every
change, both as :class:`ResetEvent` transactions and into the domain's
:class:`~usbtb.common.reset.ResetSignal` that makes the rest of the environment reset
aware. Power-on reset is just a sequence (:class:`PowerOnResetSeq`): reset is asserted
before the clock starts, so no clock edge ever reaches an unreset register.

The agent is generic: the reset may be active low or high, its assertion and its release
may each be synchronous or asynchronous, and the domain may be clocked on the rising or
the falling edge (:class:`ClkRstConfig`). A sequence item can override the configured
assertion/release timing, e.g. to check a reset synchronizer.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass

from cocotb.clock import Clock
from cocotb.handle import SimHandleBase
from cocotb.triggers import ClockCycles, FallingEdge, First, ReadWrite, RisingEdge, Timer
from cocotb.utils import get_sim_time
from pyuvm import (
    ConfigDB,
    uvm_active_passive_enum,
    uvm_agent,
    uvm_analysis_port,
    uvm_driver,
    uvm_monitor,
    uvm_sequence,
    uvm_sequence_item,
    uvm_sequencer,
)

from ..common.reset import ResetSignal, reset_key


ASSERTION_EDGE_NS = 1
"""How long an asynchronous assertion first drives the inactive level, to give the reset a
real edge. The monitor ignores inactive intervals this short."""


class ResetTiming(enum.Enum):
    SYNCHRONOUS = "synchronous"
    """Changes right after the domain's active clock edge."""
    ASYNCHRONOUS = "asynchronous"
    """Changes immediately, independent of the clock."""


class ClockEdge(enum.Enum):
    RISING = "rising"
    FALLING = "falling"

    def of(self, clock: SimHandleBase):
        return RisingEdge(clock) if self is ClockEdge.RISING else FallingEdge(clock)


@dataclass
class ClkRstConfig:
    domain: str
    clock: SimHandleBase
    reset: SimHandleBase
    period_ps: int
    reset_active_low: bool = True
    assertion: ResetTiming = ResetTiming.ASYNCHRONOUS
    release: ResetTiming = ResetTiming.SYNCHRONOUS
    active_edge: ClockEdge = ClockEdge.RISING
    is_active: uvm_active_passive_enum = uvm_active_passive_enum.UVM_ACTIVE

    @property
    def asserted_level(self) -> int:
        return 0 if self.reset_active_low else 1

    @property
    def released_level(self) -> int:
        return 1 - self.asserted_level

    def edge(self):
        """A trigger for the domain's next active clock edge."""
        return self.active_edge.of(self.clock)


class ClkRstOp(enum.Enum):
    START_CLOCK = "start clock"
    STOP_CLOCK = "stop clock"
    ASSERT_RESET = "assert reset"
    RELEASE_RESET = "release reset"
    WAIT_CYCLES = "wait cycles"


class ClkRstItem(uvm_sequence_item):
    """One operation. ``timing`` overrides the configured assertion/release timing."""

    def __init__(self, name: str = "clkrst_item", op: ClkRstOp = ClkRstOp.WAIT_CYCLES,
                 period_ps: int | None = None, cycles: int = 0, timing: ResetTiming | None = None):
        super().__init__(name)
        self.op = op
        self.period_ps = period_ps
        self.cycles = cycles
        self.timing = timing

    @classmethod
    def start_clock(cls, period_ps: int | None = None) -> ClkRstItem:
        return cls("start_clock", ClkRstOp.START_CLOCK, period_ps=period_ps)

    @classmethod
    def stop_clock(cls) -> ClkRstItem:
        return cls("stop_clock", ClkRstOp.STOP_CLOCK)

    @classmethod
    def assert_reset(cls, timing: ResetTiming | None = None) -> ClkRstItem:
        return cls("assert_reset", ClkRstOp.ASSERT_RESET, timing=timing)

    @classmethod
    def release_reset(cls, timing: ResetTiming | None = None) -> ClkRstItem:
        return cls("release_reset", ClkRstOp.RELEASE_RESET, timing=timing)

    @classmethod
    def wait_cycles(cls, cycles: int) -> ClkRstItem:
        return cls("wait_cycles", ClkRstOp.WAIT_CYCLES, cycles=cycles)

    def __str__(self) -> str:
        detail = {ClkRstOp.START_CLOCK: f" ({self.period_ps} ps)", ClkRstOp.WAIT_CYCLES: f" ({self.cycles})"}
        timing = f" ({self.timing.value})" if self.timing else ""
        return f"{self.op.value}{detail.get(self.op, '')}{timing}"


@dataclass(frozen=True)
class ResetEvent:
    domain: str
    asserted: bool
    time_ns: float


class ClkRstDriver(uvm_driver):
    def build_phase(self):
        self.cfg: ClkRstConfig = ConfigDB().get(self, "", "cfg")
        self._clock: Clock | None = None

    async def run_phase(self):
        while True:
            item = await self.seq_item_port.get_next_item()
            self.logger.debug("%s: %s", self.cfg.domain, item)
            await self._execute(item)
            self.seq_item_port.item_done()

    async def _execute(self, item: ClkRstItem) -> None:
        cfg = self.cfg
        match item.op:
            case ClkRstOp.START_CLOCK:
                if self._clock is None:
                    self._clock = Clock(cfg.clock, item.period_ps or cfg.period_ps, unit="ps")
                    self._clock.start(start_high=False)
            case ClkRstOp.STOP_CLOCK:
                if self._clock is not None:
                    self._clock.stop()
                    self._clock = None
            case ClkRstOp.ASSERT_RESET:
                await self._assert(item.timing or cfg.assertion)
            case ClkRstOp.RELEASE_RESET:
                if (item.timing or cfg.release) is ResetTiming.SYNCHRONOUS:
                    await self._after_edge()
                cfg.reset.value = cfg.released_level
            case ClkRstOp.WAIT_CYCLES:
                await ClockCycles(cfg.clock, item.cycles, rising=cfg.active_edge is ClockEdge.RISING)

    async def _assert(self, timing: ResetTiming) -> None:
        cfg = self.cfg
        if self._reset_level() == cfg.asserted_level:
            return
        if timing is ResetTiming.ASYNCHRONOUS:
            # Give the reset a real edge (deasserted -> asserted) so that a register with an
            # asynchronous reset sees the event, even at power-on.
            cfg.reset.value = cfg.released_level
            await Timer(ASSERTION_EDGE_NS, unit="ns")
        elif self._clock is not None:
            await self._after_edge()
        # A synchronous reset asserted before the clock runs is simply present at the
        # first edge, which is what power-on needs.
        cfg.reset.value = cfg.asserted_level

    async def _after_edge(self) -> None:
        """Right after the active edge: the change is sampled on the following edge."""
        await self.cfg.edge()
        await ReadWrite()

    def _reset_level(self) -> int | None:
        value = self.cfg.reset.value
        return int(value) if value.is_resolvable else None


class ClkRstMonitor(uvm_monitor):
    def build_phase(self):
        self.cfg: ClkRstConfig = ConfigDB().get(self, "", "cfg")
        self.reset_signal: ResetSignal = ConfigDB().get(self, "", reset_key(self.cfg.domain))
        self.ap = uvm_analysis_port("ap", self)

    async def run_phase(self):
        previous = None
        while True:
            asserted = self._asserted()
            if not asserted and not await self._release_confirmed():
                continue
            if asserted != previous:
                previous = asserted
                if asserted:
                    self.reset_signal.assert_reset()
                else:
                    self.reset_signal.release()
                self.logger.info("%s reset %s", self.cfg.domain, "asserted" if asserted else "released")
                self.ap.write(ResetEvent(self.cfg.domain, asserted, get_sim_time(unit="ns")))
            await self.cfg.reset.value_change

    async def _release_confirmed(self) -> bool:
        """An asynchronous assertion first drives reset inactive for an instant, only to
        give it a real edge; that instant is not a release. A synchronous release counts
        once an active clock edge sees it, an asynchronous one once it outlasts that instant."""
        if self.cfg.release is ResetTiming.SYNCHRONOUS:
            confirmation = self.cfg.edge()
        else:
            confirmation = Timer(ASSERTION_EDGE_NS * 1000 + 1, unit="ps")
        return await First(confirmation, self.cfg.reset.value_change) is confirmation and not self._asserted()

    def _asserted(self) -> bool:
        """An unknown level (before the first drive) counts as asserted."""
        value = self.cfg.reset.value
        return not value.is_resolvable or int(value) == self.cfg.asserted_level


class ClkRstAgent(uvm_agent):
    def build_phase(self):
        self.cfg: ClkRstConfig = ConfigDB().get(self, "", "cfg")
        ConfigDB().set(self, "*", "cfg", self.cfg)
        self.monitor = ClkRstMonitor("monitor", self)
        if self.cfg.is_active == uvm_active_passive_enum.UVM_ACTIVE:
            self.sequencer = uvm_sequencer("sequencer", self)
            self.driver = ClkRstDriver("driver", self)

    def connect_phase(self):
        if self.cfg.is_active == uvm_active_passive_enum.UVM_ACTIVE:
            self.driver.seq_item_port.connect(self.sequencer.seq_item_export)


# Sequences ---------------------------------------------------------------------------------

class ClkRstSeq(uvm_sequence):
    """Base: runs a fixed list of operations."""

    def operations(self) -> list[ClkRstItem]:
        return []

    async def body(self):
        for item in self.operations():
            await self.start_item(item)
            await self.finish_item(item)


class PowerOnResetSeq(ClkRstSeq):
    """Reset asserted, then the clock starts, then the release after ``hold_cycles``
    (assertion and release timing as configured for the domain)."""

    def __init__(self, name: str = "power_on_reset", period_ps: int | None = None, hold_cycles: int = 8):
        super().__init__(name)
        self.period_ps = period_ps
        self.hold_cycles = hold_cycles

    def operations(self) -> list[ClkRstItem]:
        return [ClkRstItem.assert_reset(), ClkRstItem.start_clock(self.period_ps),
                ClkRstItem.wait_cycles(self.hold_cycles), ClkRstItem.release_reset()]


class ResetPulseSeq(ClkRstSeq):
    """A reset in the middle of a test, held for ``hold_cycles``. ``assertion`` and
    ``release`` override the domain's configured timing."""

    def __init__(self, name: str = "reset_pulse", hold_cycles: int = 8,
                 assertion: ResetTiming | None = None, release: ResetTiming | None = None):
        super().__init__(name)
        self.hold_cycles = hold_cycles
        self.assertion = assertion
        self.release = release

    def operations(self) -> list[ClkRstItem]:
        return [ClkRstItem.assert_reset(self.assertion), ClkRstItem.wait_cycles(self.hold_cycles),
                ClkRstItem.release_reset(self.release)]
