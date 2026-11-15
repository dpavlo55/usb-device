"""Exact integer timing helpers for USB full-speed (12 Mbit/s)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator

FS_BIT_RATE = 12_000_000
FS_BIT_PERIOD_FS = 1_000_000_000_000_000 // FS_BIT_RATE
FS_BIT_REMAINDER_FS = 1_000_000_000_000_000 % FS_BIT_RATE
FS_BIT_TIME_FS = FS_BIT_PERIOD_FS
FS_CELL_PERIODS_FS = (83_333_333, 83_333_333, 83_333_334)
FS_SYNC_BITS = (0, 0, 0, 0, 0, 0, 0, 1)
FS_EOP_CELLS = 2
FS_RESET_DETECT_FS = 2_500_000_000
FS_RESET_DETECT_BITS = 30
FS_RESET_DEFAULT_FS = 10_000_000_000_000


@dataclass(frozen=True, slots=True)
class FSTimingScheduler:
    """Quotient/remainder scheduler with no floating point arithmetic."""

    origin_fs: int = 0
    cell_index: int = 0

    def __post_init__(self) -> None:
        if self.origin_fs < 0 or self.cell_index < 0:
            raise ValueError("timing values must not be negative")

    def period_fs(self, index: int | None = None) -> int:
        i = self.cell_index if index is None else index
        if i < 0:
            raise ValueError("cell index must not be negative")
        return FS_CELL_PERIODS_FS[i % 3]

    def time_fs(self, index: int | None = None) -> int:
        i = self.cell_index if index is None else index
        if i < 0:
            raise ValueError("cell index must not be negative")
        if i < self.cell_index:
            raise ValueError("cannot address a cell before the scheduler origin")
        delta = i - self.cell_index
        q, r = divmod(delta, 3)
        return self.origin_fs + q * sum(FS_CELL_PERIODS_FS) + sum(FS_CELL_PERIODS_FS[:r])

    def advance(self, cells: int = 1) -> "FSTimingScheduler":
        if cells < 0:
            raise ValueError("cannot move timing backwards")
        return FSTimingScheduler(self.time_fs(self.cell_index + cells), self.cell_index + cells)

    def periods(self, count: int) -> Iterator[int]:
        if count < 0:
            raise ValueError("count must not be negative")
        for i in range(count):
            yield self.period_fs(self.cell_index + i)


def cell_period_fs(index: int) -> int:
    return FSTimingScheduler().period_fs(index)


def timeout_fs(bits: int, start_cell: int = 0) -> int:
    if bits < 0:
        raise ValueError("timeout bits must not be negative")
    return FSTimingScheduler().time_fs(start_cell + bits) - FSTimingScheduler().time_fs(start_cell)


def reset_duration_fs(duration_fs: int | None = None) -> int:
    """Return a valid host reset duration, defaulting to the 10 ms reset."""
    value = FS_RESET_DEFAULT_FS if duration_fs is None else duration_fs
    if value < FS_RESET_DETECT_FS:
        raise ValueError("USB reset must be at least the 2.5 us detect threshold")
    return value


def is_reset_duration(duration_fs: int) -> bool:
    return duration_fs >= FS_RESET_DETECT_FS


FsBitScheduler = FSTimingScheduler