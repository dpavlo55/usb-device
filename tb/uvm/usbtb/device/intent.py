"""What the device software asked the hardware to do, published for the scoreboard.

The scoreboard checks the hardware against these: data armed for IN must appear on the
wire unchanged, data on the wire must arrive in the buffer unchanged, a stalled endpoint
must answer STALL.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..ch9.constants import Direction


@dataclass(frozen=True, slots=True)
class InDataArmed:
    endpoint: int
    data: bytes


@dataclass(frozen=True, slots=True)
class OutDataReceived:
    endpoint: int
    data: bytes


@dataclass(frozen=True, slots=True)
class SetupRead:
    data: bytes


@dataclass(frozen=True, slots=True)
class StallChanged:
    endpoint: int
    direction: Direction
    stalled: bool


@dataclass(frozen=True, slots=True)
class AddressChanged:
    address: int


Intent = InDataArmed | OutDataReceived | SetupRead | StallChanged | AddressChanged
