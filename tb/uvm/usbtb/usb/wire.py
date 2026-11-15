"""The single point where the UVM environment reuses the repository's USB reference codec.

``tb/cocotb/models`` already holds a tested, simulator-independent USB full-speed codec
(NRZI, bit stuffing, CRC5/CRC16, packet encode/decode, cell timing). Everything in
``usbtb`` imports it from here, so the coupling to that package is in one place.
"""
from __future__ import annotations

from models.usb_fs_codec import decode_packet, encode_packet
from models.usb_fs_timing import FS_CELL_PERIODS_FS, FS_RESET_DETECT_FS, FSTimingScheduler
from models.usb_fs_types import (
    DataPacket,
    DecodeResult,
    HandshakePacket,
    LineState,
    Packet,
    Pid,
    SofPacket,
    TokenPacket,
    WireCell,
)

BIT_TIME_FS = sum(FS_CELL_PERIODS_FS) / len(FS_CELL_PERIODS_FS)
"""One full-speed bit time (83.33 ns) in femtoseconds."""

US_FS = 1_000_000_000
MS_FS = 1_000 * US_FS
LOW_SPEED_BIT_FS = 666_667_000


def bits_to_fs(bits: float) -> int:
    return round(bits * BIT_TIME_FS)


def fs_to_bits(duration_fs: float) -> float:
    return duration_fs / BIT_TIME_FS


DATA_PIDS = (Pid.DATA0, Pid.DATA1)
TOKEN_PIDS = (Pid.SETUP, Pid.IN, Pid.OUT)
HANDSHAKE_PIDS = (Pid.ACK, Pid.NAK, Pid.STALL)


def other_data_pid(pid: Pid) -> Pid:
    return Pid.DATA1 if pid is Pid.DATA0 else Pid.DATA0


__all__ = [
    "BIT_TIME_FS", "DATA_PIDS", "DataPacket", "DecodeResult", "FSTimingScheduler", "FS_RESET_DETECT_FS",
    "HANDSHAKE_PIDS", "HandshakePacket", "LOW_SPEED_BIT_FS", "LineState", "MS_FS", "Packet", "Pid",
    "SofPacket", "TOKEN_PIDS", "TokenPacket", "US_FS", "WireCell", "bits_to_fs", "decode_packet",
    "encode_packet", "fs_to_bits", "other_data_pid",
]
