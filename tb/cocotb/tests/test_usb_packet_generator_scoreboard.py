from __future__ import annotations

import pytest
from cocotb.types import Logic, LogicArray

from tb.cocotb.models.usb_fs_codec import crc16_usb, encode_packet_bytes
from tb.cocotb.models.usb_fs_types import DataPacket, HandshakePacket, Pid
from tb.cocotb.models.usb_tx_scoreboard import ScoreboardError, UsbTxScoreboard


def _feed(scoreboard: UsbTxScoreboard, values: tuple[int, ...]) -> None:
    for index, value in enumerate(values):
        scoreboard.observe(1, value, int(index == len(values) - 1))


def test_scoreboard_accepts_handshake_and_data_crc_bytes() -> None:
    handshake = HandshakePacket(Pid.ACK)
    data = DataPacket(Pid.DATA1, b"123456789")
    assert crc16_usb(data.payload) == 0xB4C8
    assert encode_packet_bytes(data) == (0x4B, *b"123456789", 0xC8, 0xB4)
    scoreboard = UsbTxScoreboard((handshake, data))
    _feed(scoreboard, (0x80, *encode_packet_bytes(handshake)))
    _feed(scoreboard, (0x80, *encode_packet_bytes(data)))
    scoreboard.assert_complete()


@pytest.mark.parametrize("pid,expected_pid", ((Pid.ACK, 0xD2), (Pid.NAK, 0x5A), (Pid.STALL, 0x1E)))
def test_handshake_is_sync_then_pid(pid: Pid, expected_pid: int) -> None:
    scoreboard = UsbTxScoreboard((HandshakePacket(pid),))
    scoreboard.observe(packet_tvalid=1, packet_tdata=0x80, packet_tlast=0)
    assert not scoreboard.complete
    scoreboard.observe(packet_tvalid=1, packet_tdata=expected_pid, packet_tlast=1)
    scoreboard.assert_complete()
    assert scoreboard.observed == [0x80, expected_pid]


@pytest.mark.parametrize("packet_index", (0, 1))
def test_scoreboard_requires_sync_on_every_packet(packet_index: int) -> None:
    packets = (HandshakePacket(Pid.ACK), HandshakePacket(Pid.NAK))
    scoreboard = UsbTxScoreboard(packets)
    for packet in packets[:packet_index]:
        _feed(scoreboard, (0x80, *encode_packet_bytes(packet)))
    with pytest.raises(ScoreboardError, match="expected=0x80"):
        _feed(scoreboard, encode_packet_bytes(packets[packet_index]))


def test_scoreboard_rejects_duplicate_sync() -> None:
    scoreboard = UsbTxScoreboard((HandshakePacket(Pid.ACK),))
    scoreboard.observe(1, 0x80, 0)
    with pytest.raises(ScoreboardError, match="expected=0xD2 observed=0x80"):
        scoreboard.observe(1, 0x80, 0)


@pytest.mark.parametrize("pid", (Pid.DATA0, Pid.DATA1))
@pytest.mark.parametrize("length", (0, 1, 64))
def test_data_payload_boundaries(pid: Pid, length: int) -> None:
    packet = DataPacket(pid, bytes(range(length)))
    scoreboard = UsbTxScoreboard((packet,))
    if not length:
        assert encode_packet_bytes(packet) == (pid.byte, 0x00, 0x00)
    _feed(scoreboard, (0x80, *encode_packet_bytes(packet)))
    scoreboard.assert_complete()


@pytest.mark.parametrize("byte_index", (0, 1, 2, -2, -1))
def test_scoreboard_rejects_corrupt_sync_pid_payload_or_crc(byte_index: int) -> None:
    packet = DataPacket(Pid.DATA0, b"payload")
    values = [0x80, *encode_packet_bytes(packet)]
    values[byte_index] ^= 1
    scoreboard = UsbTxScoreboard((packet,))
    with pytest.raises(ScoreboardError, match="byte mismatch"):
        _feed(scoreboard, tuple(values))


def test_scoreboard_rejects_extra_bytes_after_a_complete_packet() -> None:
    scoreboard = UsbTxScoreboard((HandshakePacket(Pid.ACK),))
    _feed(scoreboard, (0x80, *encode_packet_bytes(HandshakePacket(Pid.ACK))))
    with pytest.raises(ScoreboardError, match="extra packet_tdata byte"):
        scoreboard.observe(1, 0, 1)


def test_scoreboard_rejects_last_contract_violations_and_unresolved_values() -> None:
    expected = (0x80, *encode_packet_bytes(HandshakePacket(Pid.NAK)))
    data_expected = (0x80, *encode_packet_bytes(DataPacket(Pid.DATA0, b"x")))
    with pytest.raises(ScoreboardError, match="before"):
        UsbTxScoreboard((DataPacket(Pid.DATA0, b"x"),)).observe(1, data_expected[0], 1)
    scoreboard = UsbTxScoreboard((HandshakePacket(Pid.NAK),))
    scoreboard.observe(1, expected[0], 0)
    with pytest.raises(ScoreboardError, match="missing packet_tlast on the final packet byte"):
        scoreboard.observe(1, expected[1], 0)
    with pytest.raises(ScoreboardError, match="missing packet_tdata transfer"):
        UsbTxScoreboard((HandshakePacket(Pid.NAK),)).assert_complete()
    scoreboard = UsbTxScoreboard((HandshakePacket(Pid.NAK),))
    _feed(scoreboard, expected)
    with pytest.raises(ScoreboardError, match="packet_tlast asserted without packet_tvalid"):
        scoreboard.observe(0, 0, 1)
    with pytest.raises(ScoreboardError, match="unresolved"):
        UsbTxScoreboard((HandshakePacket(Pid.NAK),)).observe("X", 0, 0)


@pytest.mark.parametrize("pid", (Pid.ACK, Pid.NAK))
def test_missing_packet_tlast_reports_axi_signal_and_byte(pid: Pid) -> None:
    scoreboard = UsbTxScoreboard((HandshakePacket(pid),), context="seed=3120276469")
    scoreboard.observe(1, 0x80, 0)
    with pytest.raises(ScoreboardError) as error:
        scoreboard.observe(1, pid.byte, 0)
    assert str(error.value) == (
        "missing packet_tlast on the final packet byte packet=0 byte=1 "
        f"packet_tdata=0x{pid.byte:02X} expected=1 observed=0 (seed=3120276469)"
    )


def test_scoreboard_accepts_settled_cocotb_values_and_ignores_idle_data() -> None:
    scoreboard = UsbTxScoreboard((HandshakePacket(Pid.ACK),))
    scoreboard.observe(Logic("0"), LogicArray("UUUUUUUU"), Logic("0"))
    scoreboard.observe(Logic("1"), LogicArray("10000000"), Logic("0"))
    scoreboard.observe(Logic("1"), LogicArray("11010010"), Logic("1"))
    scoreboard.assert_complete()
    scoreboard.observe(0, None, 0)


@pytest.mark.parametrize("data", (0xD2, "0xd2", "11010010"))
def test_scoreboard_accepts_byte_representations(data: object) -> None:
    scoreboard = UsbTxScoreboard((HandshakePacket(Pid.ACK),))
    scoreboard.observe(1, 0x80, 0)
    scoreboard.observe(1, data, 1)
    scoreboard.assert_complete()


@pytest.mark.parametrize(
    "valid,data,last,field",
    (
        (Logic("U"), 0, 0, "packet_tvalid"),
        (0, 0, Logic("X"), "packet_tlast"),
        (1, LogicArray("XXXXXXXX"), 1, "packet_tdata"),
    ),
)
def test_scoreboard_rejects_unresolved_active_values(valid, data, last, field) -> None:
    scoreboard = UsbTxScoreboard((HandshakePacket(Pid.ACK),))
    with pytest.raises(ScoreboardError, match=f"unresolved {field}"):
        scoreboard.observe(valid, data, last)


def test_scoreboard_rejects_reversed_crc_and_pid_complement() -> None:
    packet = DataPacket(Pid.DATA1, b"123456789")
    values = (0x80, *encode_packet_bytes(packet))
    with pytest.raises(ScoreboardError, match="byte mismatch"):
        _feed(UsbTxScoreboard((packet,)), (*values[:-2], values[-1], values[-2]))
    scoreboard = UsbTxScoreboard((packet,))
    scoreboard.observe(1, values[0], 0)
    with pytest.raises(ScoreboardError, match="byte mismatch"):
        scoreboard.observe(1, values[1] ^ 0x10, 0)


def test_scoreboard_rejects_truncated_data_packet_with_replay_context() -> None:
    packet = DataPacket(Pid.DATA0, b"x")
    scoreboard = UsbTxScoreboard((packet,), context="seed=24301")
    for value in (0x80, *encode_packet_bytes(packet)[:-1]):
        scoreboard.observe(1, value, 0)
    with pytest.raises(ScoreboardError, match="missing packet_tdata transfer.*seed=24301"):
        scoreboard.assert_complete()