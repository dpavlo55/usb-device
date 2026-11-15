"""Independent codec checks and negative controls for the serializer monitor."""
from __future__ import annotations

import pytest
from cocotb.types import Logic, LogicArray

from tb.cocotb.models.usb_fs_codec import bit_stuff, byte_to_bits, encode_packet_bytes, nrzi_encode
from tb.cocotb.models.usb_fs_types import DataPacket, LineState, Pid
from tb.cocotb.models.usb_serializer_scoreboard import (
    EOP, UsbSerializerScoreboard, line_state_value, logic_value,
)
from tb.cocotb.models.usb_tx_scoreboard import ScoreboardError


def _states(data: bytes) -> tuple[LineState, ...]:
    return nrzi_encode(bit_stuff(bit for value in data for bit in byte_to_bits(value))) + EOP


def _accept(scoreboard: UsbSerializerScoreboard, data: bytes) -> None:
    if not data:
        scoreboard.accept(0xFF, 0, 1)
    for index, value in enumerate(data):
        scoreboard.accept(value, 1, int(index == len(data) - 1))


def _observe(scoreboard: UsbSerializerScoreboard, states: tuple[LineState, ...]) -> None:
    for cycle, state in enumerate(states):
        scoreboard.observe(cycle, 1, state)


@pytest.mark.parametrize("value", range(256))
def test_all_bytes_match_independent_codec(value: int) -> None:
    data = bytes((0x80, value))
    scoreboard = UsbSerializerScoreboard()
    _accept(scoreboard, data)
    _observe(scoreboard, _states(data))
    scoreboard.assert_complete()
    assert scoreboard.observed_frames == 1


@pytest.mark.parametrize("preceding", range(9))
@pytest.mark.parametrize("leading", range(9))
def test_cross_byte_stuffing_and_residual_runs(preceding: int, leading: int) -> None:
    data = bytes((0x80, (0xFF << (8 - preceding)) & 0xFF, (1 << leading) - 1))
    scoreboard = UsbSerializerScoreboard()
    _accept(scoreboard, data)
    _observe(scoreboard, _states(data))
    scoreboard.assert_complete()


def test_sync_and_terminal_stuffing_have_literal_expected_states() -> None:
    scoreboard = UsbSerializerScoreboard()
    _accept(scoreboard, b"\x80\xFC")
    # SYNC KJKJKJKK, then two zeros, six ones, and a stuffed zero before EOP.
    expected = tuple(LineState(value) for value in "KJKJKJKKJKKKKKKKJ") + EOP
    assert _states(b"\x80\xFC") == expected
    _observe(scoreboard, expected)
    assert scoreboard.stuffed_bits == 1
    scoreboard.assert_complete()


def test_null_beats_do_not_break_a_run_or_add_data() -> None:
    scoreboard = UsbSerializerScoreboard()
    for data, keep, last in (
        (0xFF, 0, 0), (0x80, 1, 0), (0xF8, 1, 0),
        (0x00, 0, 0), (0xFF, 1, 0), (0xFF, 0, 1),
    ):
        scoreboard.accept(data, keep, last)
    _observe(scoreboard, _states(b"\x80\xF8\xFF"))
    scoreboard.assert_complete()
    assert scoreboard.accepted_beats == 6
    assert scoreboard.accepted_frames == 1


def test_empty_frame_is_only_eop() -> None:
    scoreboard = UsbSerializerScoreboard()
    scoreboard.accept(0x80, 0, 0)
    scoreboard.accept(0xFF, 0, 1)
    _observe(scoreboard, EOP)
    scoreboard.assert_complete()


def test_queued_frames_restart_nrzi_and_stuffing() -> None:
    scoreboard = UsbSerializerScoreboard()
    frames = (b"\x80\xFC", b"\x80\x1F", b"", b"\x80\xF8")
    for data in frames:
        _accept(scoreboard, data)
    _observe(scoreboard, tuple(state for data in frames for state in _states(data)))
    scoreboard.assert_complete()
    assert scoreboard.observed_frames == len(frames)


@pytest.mark.parametrize("interval", (0, 2, 3, 5, 8))
def test_rejects_non_consecutive_samples(interval: int) -> None:
    scoreboard = UsbSerializerScoreboard()
    _accept(scoreboard, b"\x80")
    scoreboard.observe(10, 1, LineState.K)
    with pytest.raises(ScoreboardError, match="cadence.*every clock"):
        scoreboard.observe(10 + interval, 1, LineState.J)


def test_rejects_missing_valid_on_the_next_clock() -> None:
    scoreboard = UsbSerializerScoreboard()
    _accept(scoreboard, b"\x80")
    scoreboard.observe(0, 1, LineState.K)
    with pytest.raises(ScoreboardError, match="cadence"):
        scoreboard.observe(1, 0)


@pytest.mark.parametrize("symbol", (-3, -2, -1))
def test_consecutive_samples_include_both_se0_symbols_and_final_j(symbol: int) -> None:
    states = _states(b"\x80")
    scoreboard = UsbSerializerScoreboard()
    _accept(scoreboard, b"\x80")
    index = len(states) + symbol
    for position in range(index):
        scoreboard.observe(position, 1, states[position])
    with pytest.raises(ScoreboardError, match="cadence"):
        scoreboard.observe(index + 1, 1, states[index])


@pytest.mark.parametrize("symbol", (-3, -2, -1))
def test_rejects_corrupt_eop(symbol: int) -> None:
    states = list(_states(b"\x80"))
    states[symbol] = LineState.K
    scoreboard = UsbSerializerScoreboard()
    _accept(scoreboard, b"\x80")
    with pytest.raises(ScoreboardError, match="symbol mismatch"):
        _observe(scoreboard, tuple(states))


@pytest.mark.parametrize("mutation", ("missing_terminal_stuff", "extra_sync", "msb_first", "se1"))
def test_rejects_incorrect_serialization(mutation: str) -> None:
    data = b"\x80\xFC"
    states = _states(data)
    if mutation == "missing_terminal_stuff":
        states = states[:-4] + EOP
    elif mutation == "extra_sync":
        states = _states(b"\x80" + data)
    elif mutation == "msb_first":
        states = nrzi_encode(bit_stuff(
            bit for value in data for bit in reversed(byte_to_bits(value))
        )) + EOP
    else:
        states = (LineState.SE1,) + states[1:]
    scoreboard = UsbSerializerScoreboard()
    _accept(scoreboard, data)
    with pytest.raises(ScoreboardError, match="symbol mismatch"):
        _observe(scoreboard, states)


def test_rejects_output_without_input_or_after_eop() -> None:
    scoreboard = UsbSerializerScoreboard()
    with pytest.raises(ScoreboardError, match="without accepted input"):
        scoreboard.observe(0, 1, LineState.K)
    _accept(scoreboard, b"\x80")
    states = _states(b"\x80")
    _observe(scoreboard, states)
    with pytest.raises(ScoreboardError, match="without accepted input"):
        scoreboard.observe(len(states), 1, LineState.J)


def test_rejects_early_eop_and_missing_tlast() -> None:
    scoreboard = UsbSerializerScoreboard()
    scoreboard.accept(0x80, 1, 0)
    _observe(scoreboard, _states(b"\x80")[:-3])
    with pytest.raises(ScoreboardError, match="missing USB output or TLAST"):
        scoreboard.assert_complete()
    with pytest.raises(ScoreboardError, match="without accepted input"):
        scoreboard.observe(8, 1, LineState.SE0)


def test_queued_failure_retains_the_originating_frame_context() -> None:
    scoreboard = UsbSerializerScoreboard("first_frame")
    _accept(scoreboard, b"\x80\xFC")
    scoreboard.context = "second_frame"
    _accept(scoreboard, b"\x80\xD2")
    with pytest.raises(ScoreboardError, match="first_frame") as error:
        scoreboard.observe(0, 1, LineState.SE1)
    assert "second_frame" not in str(error.value)


def test_completion_detects_truncation_with_context() -> None:
    scoreboard = UsbSerializerScoreboard("seed=24301")
    _accept(scoreboard, b"\x80")
    scoreboard.observe(0, 1, LineState.K)
    with pytest.raises(ScoreboardError, match="missing USB output.*seed=24301"):
        scoreboard.assert_complete()


def test_idle_state_is_unspecified_and_start_latency_is_not_fixed() -> None:
    scoreboard = UsbSerializerScoreboard()
    _accept(scoreboard, b"\x80")
    for cycle in range(100):
        scoreboard.observe(cycle, 0, LineState.SE1)
    scoreboard.observe(100, 1, LineState.K)


@pytest.mark.parametrize("value,name", ((Logic("X"), "valid"), (LogicArray("UU"), "line")))
def test_unresolved_logic_is_a_useful_failure(value, name: str) -> None:
    with pytest.raises(ScoreboardError, match=f"unresolved {name}"):
        logic_value(value, name)


def test_resolved_simulator_logic_values() -> None:
    assert logic_value(Logic("1"), "valid") == 1
    assert logic_value(LogicArray("00"), "line") == 0


@pytest.mark.parametrize("position,state", (
    (0, LineState.J), (1, LineState.K), (2, LineState.SE0), (3, LineState.SE1),
))
def test_usb_line_state_uses_declared_vhdl_enum_order(position: int, state: LineState) -> None:
    assert line_state_value(position) is state


@pytest.mark.parametrize("position", (-1, 4, 5))
def test_invalid_usb_line_state_is_rejected(position: int) -> None:
    with pytest.raises(ScoreboardError, match="invalid usb_line_state"):
        line_state_value(position)


@pytest.mark.parametrize("value,residual,terminal_stuff", (
    (0, 0, False), (3, 1, False), (1, 2, False), (6, 3, False),
    (8194, 4, False), (12294, 5, False), (14338, 0, True),
))
def test_valid_data_packets_cover_all_crc_to_eop_stuffing_cases(
    value: int, residual: int, terminal_stuff: bool,
) -> None:
    packet = DataPacket(Pid.DATA0, value.to_bytes(2, "little"))
    data = bytes((0x80, *encode_packet_bytes(packet)))
    bits = tuple(bit for byte in data for bit in byte_to_bits(byte))
    stuffed = bit_stuff(bits)
    trailing_ones = next(index for index, bit in enumerate(reversed(stuffed)) if bit == 0)
    assert trailing_ones == residual
    assert (bits[-1] == 1 and stuffed[-1] == 0) == terminal_stuff
    scoreboard = UsbSerializerScoreboard()
    _accept(scoreboard, data)
    _observe(scoreboard, nrzi_encode(stuffed) + EOP)
    scoreboard.assert_complete()
