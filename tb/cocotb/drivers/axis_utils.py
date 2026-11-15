"""Small helpers shared by the AXI4-Stream testbenches."""
from __future__ import annotations

import random

from models.usb_ram_adapter_model import StreamBeat

THROTTLE_MODES = ("always", "random", "bursty", "sparse")


class Throttle:
    """Per-cycle decision whether a valid (or ready) is offered."""

    def __init__(self, rng: random.Random, mode: str) -> None:
        if mode not in THROTTLE_MODES:
            raise ValueError(f"unknown throttle mode {mode}")
        self.rng = rng
        self.mode = mode
        self._burst_on = True

    def __call__(self) -> bool:
        if self.mode == "always":
            return True
        if self.mode == "random":
            return self.rng.random() < 0.5
        if self.mode == "sparse":
            return self.rng.random() < 0.2
        if self.rng.random() < 0.1:
            self._burst_on = not self._burst_on
        return self._burst_on


def resolved(value, name: str) -> int:
    """The integer value of a signal, or ``ValueError`` if it holds X/U/Z."""
    if not value.is_resolvable:
        raise ValueError(f"{name} is unresolved: {value}")
    return int(value)


def beats_differ(first: StreamBeat, second: StreamBeat) -> bool:
    """True if two offers of the same stalled beat are not identical (data ignored when tkeep = 0)."""
    if (first.keep, first.dest, first.last) != (second.keep, second.dest, second.last):
        return True
    return bool(first.keep & 1) and first.data != second.data
