from __future__ import annotations

import pytest

from tb.cocotb.models.usb_fs_codec import (
    bit_stuff,
    bit_unstuff,
    crc5_usb,
    crc16_usb,
    decode_packet,
    encode_packet,
    encode_packet_bytes,
    nrzi_encode,
)
from tb.cocotb.models.usb_fs_types import (
    DataPacket,
    DecodeError,
    Fault,
    FaultKind,
    HandshakePacket,
    LineState,
    Pid,
    TokenPacket,
)


def test_pid_bytes_are_single_usb_pid_bytes() -> None:
    expected = {
        Pid.SETUP: 0x2D,
        Pid.OUT: 0xE1,
        Pid.IN: 0x69,
        Pid.SOF: 0xA5,
        Pid.DATA0: 0xC3,
        Pid.DATA1: 0x4B,
        Pid.ACK: 0xD2,
        Pid.NAK: 0x5A,
        Pid.STALL: 0x1E,
    }
    assert {pid: pid.byte for pid in expected} == expected


def test_token_and_crc_golden_vectors() -> None:
    token = TokenPacket(Pid.SETUP, 0, 0)
    assert encode_packet_bytes(token) == (0x2D, 0x00, 0x10)
    assert crc5_usb((0,) * 11) == 0x02
    assert crc16_usb(b"123456789") == 0xB4C8


def test_sync_is_kjkjkjkk_and_round_trip() -> None:
    sync_states = nrzi_encode((0, 0, 0, 0, 0, 0, 0, 1))
    assert sync_states == (LineState.K, LineState.J, LineState.K, LineState.J, LineState.K, LineState.J, LineState.K, LineState.K)
    packet = DataPacket(Pid.DATA0, b"USB")
    result = decode_packet(encode_packet(packet))
    assert result.ok
    assert result.packet == packet


def test_malformed_pid_crc_stuffing_se1_and_eop_are_reported() -> None:
    packet = TokenPacket(Pid.OUT, 4, 2)
    assert decode_packet(encode_packet(packet, [Fault(FaultKind.PID_COMPLEMENT)])).error.code.value == "pid_complement"
    assert decode_packet(encode_packet(packet, [Fault(FaultKind.CRC5)])).error.code.value == "crc5"
    assert decode_packet(encode_packet(DataPacket(Pid.DATA0, b"payload"), [Fault(FaultKind.CRC16)])).error.code.value == "crc16"
    assert decode_packet(encode_packet(packet, [Fault(FaultKind.SE1, 8)])).error.code.value == "se1"
    assert decode_packet(encode_packet(packet, [Fault(FaultKind.TRUNCATE, 8)])).error.code.value in {"eop", "short", "sync"}
    stuffed = decode_packet(encode_packet(DataPacket(Pid.DATA0, b"\xff\xff"), [Fault(FaultKind.STUFF)]) )
    assert stuffed.error.code.value == "stuff"
    assert bit_stuff((1,) * 6) == (1, 1, 1, 1, 1, 1, 0)


def test_full_speed_rejects_unsupported_handshakes_and_missing_stuff_bits() -> None:
    with pytest.raises(ValueError, match="full-speed handshake"):
        HandshakePacket(Pid.NYET)
    with pytest.raises(DecodeError, match="missing stuffed zero"):
        bit_unstuff((1,) * 6)