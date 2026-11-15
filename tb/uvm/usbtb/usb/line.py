"""The USB wire: driving line states, observing them, and turning them into packets.

:class:`LineObserver` reports *runs* (a line state and how long it lasted) from the D+/D-
transitions, so it needs no sampling clock and works for whoever drives the bus.
:class:`PacketAssembler` is pure Python: it turns runs into packets and bus conditions
(reset, resume, long idle) and is shared by the bus monitor and the reference device.
"""
from __future__ import annotations

import enum
from collections.abc import AsyncIterator, Callable, Iterable
from dataclasses import dataclass, field

from cocotb.handle import SimHandleBase
from cocotb.triggers import First, ReadOnly, Timer
from cocotb.utils import get_sim_time

from .wire import (
    BIT_TIME_FS,
    FS_RESET_DETECT_FS,
    FSTimingScheduler,
    LineState,
    MS_FS,
    Packet,
    WireCell,
    decode_packet,
    encode_packet,
    fs_to_bits,
)


class Source(enum.Enum):
    HOST = "host"
    DEVICE = "device"


@dataclass(frozen=True, slots=True)
class ObservedPacket:
    """A packet seen on the bus. ``end_fs`` is the SE0-to-J transition that ends its EOP."""

    packet: Packet | None
    source: Source
    start_fs: int
    end_fs: int
    errors: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.packet is not None and not self.errors

    @property
    def pid(self):
        return getattr(self.packet, "pid", None)

    def __str__(self) -> str:
        what = self.packet if self.ok else f"corrupt packet {self.errors}"
        return f"{self.source.value} @{self.start_fs / 1e6:.1f}ns: {what}"


class LineCondition(enum.Enum):
    RESET = "reset"
    DETACH = "detach"
    RESUME = "resume"
    IDLE = "idle"


@dataclass(frozen=True, slots=True)
class LineEvent:
    condition: LineCondition
    source: Source
    start_fs: int
    duration_fs: int

    def __str__(self) -> str:
        return f"{self.condition.value} by {self.source.value} for {self.duration_fs / MS_FS:.3f} ms"


def _logic_level(value) -> int | None:
    text = str(value).upper()
    if text in ("1", "H"):
        return 1
    if text in ("0", "L"):
        return 0
    return None


def line_state(dp: SimHandleBase, dn: SimHandleBase) -> LineState:
    """The differential state of the resolved bus; anything unresolved reads as SE1."""
    levels = (_logic_level(dp.value), _logic_level(dn.value))
    for state in LineState:
        if state.differential == levels:
            return state
    return LineState.SE1


def is_driving(dp_drive: SimHandleBase) -> bool:
    return _logic_level(dp_drive.value) is not None


class LineDriver:
    """Drives one party's D+/D- inputs of the testbench harness; releasing means 'Z'."""

    def __init__(self, dp: SimHandleBase, dn: SimHandleBase):
        self.dp = dp
        self.dn = dn

    def drive(self, state: LineState) -> None:
        self.dp.value, self.dn.value = state.differential

    def release(self) -> None:
        self.dp.value = "Z"
        self.dn.value = "Z"

    async def hold(self, state: LineState, duration_fs: int) -> None:
        self.drive(state)
        await Timer(duration_fs, unit="fs")

    async def send_cells(self, cells: Iterable[WireCell | LineState]) -> None:
        """Drive cells at the full-speed bit rate, then release the bus (idle J via pull-up)."""
        schedule = FSTimingScheduler()
        for index, cell in enumerate(cells):
            self.drive(cell.state if isinstance(cell, WireCell) else cell)
            await Timer(schedule.period_fs(index), unit="fs")
        self.release()

    async def send_packet(self, packet: Packet, faults=()) -> None:
        await self.send_cells(encode_packet(packet, faults=faults))


@dataclass(frozen=True, slots=True)
class LineRun:
    state: LineState
    start_fs: int
    duration_fs: int
    source: Source = Source.HOST

    @property
    def bits(self) -> int:
        return max(1, round(fs_to_bits(self.duration_fs)))


class LineObserver:
    """Yields one :class:`LineRun` each time the bus changes state.

    ``source`` tells who drives the bus; it is asked at the start of every run.
    """

    def __init__(self, dp: SimHandleBase, dn: SimHandleBase,
                 source: Callable[[], Source] = lambda: Source.HOST):
        self.dp = dp
        self.dn = dn
        self.source = source

    def state(self) -> LineState:
        return line_state(self.dp, self.dn)

    async def runs(self) -> AsyncIterator[LineRun]:
        await ReadOnly()
        state, start, source = self.state(), self._now(), self.source()
        while True:
            await First(self.dp.value_change, self.dn.value_change)
            await ReadOnly()
            new_state = self.state()
            if new_state is state:
                continue
            now = self._now()
            yield LineRun(state, start, now - start, source)
            state, start, source = new_state, now, self.source()

    @staticmethod
    def _now() -> int:
        return round(get_sim_time(unit="fs"))


@dataclass
class PacketAssembler:
    """Turns line runs into packets and bus conditions.

    Each run says who drove it, so packets and conditions are attributed to the host
    or the device.
    """

    reset_threshold_fs: int = FS_RESET_DETECT_FS
    idle_threshold_fs: int = 3 * MS_FS
    resume_threshold_bits: int = 16
    _cells: list[LineState] = field(default_factory=list)
    _start_fs: int = 0
    _source: Source = Source.HOST

    def feed(self, run: LineRun) -> list[ObservedPacket | LineEvent]:
        source = run.source
        if self._cells:
            return self._in_packet(run)
        match run.state:
            case LineState.K if run.bits > self.resume_threshold_bits:
                return [LineEvent(LineCondition.RESUME, source, run.start_fs, run.duration_fs)]
            case LineState.K:
                self._cells = [LineState.K] * run.bits
                self._start_fs, self._source = run.start_fs, source
            case LineState.SE0 if run.duration_fs >= self.reset_threshold_fs:
                # SE0 that the host drives is a bus reset; SE0 nobody drives means no pull-up.
                condition = LineCondition.RESET if source is Source.HOST else LineCondition.DETACH
                return [LineEvent(condition, source, run.start_fs, run.duration_fs)]
            case LineState.J if run.duration_fs >= self.idle_threshold_fs:
                return [LineEvent(LineCondition.IDLE, source, run.start_fs, run.duration_fs)]
        return []

    def _in_packet(self, run: LineRun) -> list[ObservedPacket | LineEvent]:
        if run.state is not LineState.SE0:
            self._cells += [run.state] * run.bits
            return []
        cells, self._cells = self._cells, []
        end_fs = run.start_fs + run.duration_fs
        if run.duration_fs >= self.reset_threshold_fs:
            aborted = ObservedPacket(None, self._source, self._start_fs, run.start_fs, ("reset during packet",))
            return [aborted, LineEvent(LineCondition.RESET, self._source, run.start_fs, run.duration_fs)]
        result = decode_packet(cells + [LineState.SE0] * run.bits + [LineState.J])
        errors = tuple(f"{error.code.value}: {error.message}" for error in result.errors)
        return [ObservedPacket(result.packet, self._source, self._start_fs, end_fs, errors)]

    def reset(self) -> None:
        self._cells = []


__all__ = ["BIT_TIME_FS", "LineCondition", "LineDriver", "LineEvent", "LineObserver", "LineRun",
           "ObservedPacket", "PacketAssembler", "Source", "is_driving", "line_state"]
