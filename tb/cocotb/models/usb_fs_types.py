"""Domain objects shared by the USB full-speed model.

The model deliberately uses values that are independent of cocotb handles.  A
wire cell is one full-speed bit time and packet payloads are immutable bytes.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, IntEnum
from typing import Optional, Tuple


class LineState(Enum):
    J = "J"
    K = "K"
    SE0 = "SE0"
    SE1 = "SE1"

    @property
    def differential(self) -> tuple[int, int]:
        return {self.J: (1, 0), self.K: (0, 1), self.SE0: (0, 0), self.SE1: (1, 1)}[self]


class Pid(IntEnum):
    OUT = 0x1
    ACK = 0x2
    DATA0 = 0x3
    PING = 0x4
    SOF = 0x5
    NYET = 0x6
    DATA2 = 0x7
    SPLIT = 0x8
    IN = 0x9
    NAK = 0xA
    DATA1 = 0xB
    PRE = 0xC
    SETUP = 0xD
    STALL = 0xE
    RESERVED = 0xF

    @property
    def byte(self) -> int:
        return int(self) | ((int(self) ^ 0xF) << 4)

    @classmethod
    def from_byte(cls, value: int) -> "Pid":
        if not 0 <= value <= 0xFF:
            raise ValueError("PID byte must be in range 0..255")
        low = value & 0x0F
        if ((value >> 4) & 0x0F) != (low ^ 0x0F):
            raise ValueError("PID complement mismatch")
        return cls(low)


PID = Pid
PacketPid = Pid
PacketKind = Pid
PacketType = Pid


@dataclass(frozen=True, slots=True)
class TokenPacket:
    pid: Pid
    address: int
    endpoint: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "pid", Pid(self.pid))
        if self.pid not in (Pid.SETUP, Pid.OUT, Pid.IN):
            raise ValueError("token PID must be SETUP, OUT, or IN")
        if not 0 <= self.address <= 127:
            raise ValueError("USB address must be in range 0..127")
        if not 0 <= self.endpoint <= 15:
            raise ValueError("USB endpoint must be in range 0..15")


@dataclass(frozen=True, slots=True)
class SofPacket:
    frame: int
    pid: Pid = Pid.SOF

    def __post_init__(self) -> None:
        object.__setattr__(self, "pid", Pid(self.pid))
        if self.pid is not Pid.SOF:
            raise ValueError("SOF packet must use the SOF PID")
        if not 0 <= self.frame <= 0x7FF:
            raise ValueError("SOF frame must be in range 0..2047")


@dataclass(frozen=True, slots=True)
class DataPacket:
    pid: Pid
    payload: bytes

    def __post_init__(self) -> None:
        object.__setattr__(self, "pid", Pid(self.pid))
        if self.pid not in (Pid.DATA0, Pid.DATA1):
            raise ValueError("data PID must be DATA0 or DATA1")
        object.__setattr__(self, "payload", bytes(self.payload))
        if len(self.payload) > 1023:
            raise ValueError("full-speed data payload must be at most 1023 bytes")


@dataclass(frozen=True, slots=True)
class HandshakePacket:
    pid: Pid

    def __post_init__(self) -> None:
        object.__setattr__(self, "pid", Pid(self.pid))
        if self.pid not in (Pid.ACK, Pid.NAK, Pid.STALL):
            raise ValueError("full-speed handshake PID must be ACK, NAK, or STALL")


@dataclass(frozen=True, slots=True)
class SpecialPacket:
    pid: Pid

    def __post_init__(self) -> None:
        object.__setattr__(self, "pid", Pid(self.pid))
        if self.pid is not Pid.PRE:
            raise ValueError("full-speed special packet must use the PRE PID")


Packet = TokenPacket | SofPacket | DataPacket | HandshakePacket | SpecialPacket


@dataclass(frozen=True, slots=True)
class WireCell:
    state: LineState
    time_fs: Optional[int] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "state", LineState(self.state))
        if self.time_fs is not None and self.time_fs < 0:
            raise ValueError("cell time must not be negative")


class FaultKind(Enum):
    PID_COMPLEMENT = "pid_complement"
    CRC5 = "crc5"
    CRC16 = "crc16"
    DATA = "data"
    STUFF = "stuff"
    SE1 = "se1"
    EOP = "eop"
    TRUNCATE = "truncate"


@dataclass(frozen=True, slots=True)
class Fault:
    kind: FaultKind
    index: int = 0
    value: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", FaultKind(self.kind))
        if self.index < 0:
            raise ValueError("fault index must not be negative")


class DecodeErrorCode(Enum):
    EMPTY = "empty"
    SYNC = "sync"
    SE1 = "se1"
    PID = "pid"
    PID_COMPLEMENT = "pid_complement"
    SHORT = "short"
    CRC5 = "crc5"
    CRC16 = "crc16"
    STUFF = "stuff"
    EOP = "eop"


class DecodeError(Exception):
    """A structured, human-readable packet decoding error."""

    __slots__ = ("code", "message", "cell_index")

    def __init__(self, code: DecodeErrorCode | str, message: str, cell_index: int | None = None):
        self.code = DecodeErrorCode(code) if isinstance(code, str) else code
        self.message = message
        self.cell_index = cell_index
        super().__init__(message)

    def __repr__(self) -> str:
        return f"DecodeError({self.code.value!r}, {self.message!r}, {self.cell_index!r})"


@dataclass(frozen=True, slots=True)
class DecodeResult:
    packet: Optional[Packet]
    errors: Tuple[DecodeError, ...] = ()
    stuffed_bits: int = 0
    consumed_cells: int = 0
    eop: bool = False

    @property
    def ok(self) -> bool:
        return self.packet is not None and not self.errors

    @property
    def error(self) -> Optional[DecodeError]:
        return self.errors[0] if self.errors else None


@dataclass(frozen=True, slots=True)
class TransactionResult:
    success: bool
    pid: Optional[Pid] = None
    packet: Optional[Packet] = None
    payload: bytes = b""
    error: Optional[str] = None
    diagnostics: Tuple[str, ...] = ()
    timed_out: bool = False
    sent_packets: Tuple[Packet, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "payload", bytes(self.payload))
        object.__setattr__(self, "diagnostics", tuple(self.diagnostics))
        object.__setattr__(self, "sent_packets", tuple(self.sent_packets))