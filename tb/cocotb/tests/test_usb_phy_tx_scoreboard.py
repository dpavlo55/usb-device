"""Validate the PHY checker using independent codec output and corruptions."""
from __future__ import annotations

import pytest

from tb.cocotb.models.usb_fs_codec import bit_stuff, byte_to_bits, crc16_usb, nrzi_encode
from tb.cocotb.models.usb_fs_types import LineState
from tb.cocotb.models.usb_phy_tx_scoreboard import UsbPhyTxScoreboard
from tb.cocotb.models.usb_tx_scoreboard import ScoreboardError


def bits(data: bytes) -> tuple[int, ...]:
    return tuple(bit for value in data for bit in byte_to_bits(value))


def samples(data: bytes) -> list[tuple[int, int, int]]:
    states = nrzi_encode(bit_stuff(bits(data))) + (LineState.SE0, LineState.SE0, LineState.J)
    return [(1, *state.differential) for state in states for _ in range(4)]


def accept(scoreboard: UsbPhyTxScoreboard, data: bytes) -> None:
    stream = bits(data)
    for index, bit in enumerate(stream):
        scoreboard.accept(bit, 1, 0, int(index == len(stream) - 1))


def observe(scoreboard: UsbPhyTxScoreboard, waveform: list[tuple[int, int, int]], start: int = 0) -> None:
    for cycle, sample in enumerate(waveform, start):
        scoreboard.observe(cycle, *sample)


@pytest.mark.parametrize("data", (b"\x80\xd2", b"\x80\xc3\xff\xff", b"\x80\xfc", bytes(range(256))))
def test_correct_encoding_and_all_four_samples(data: bytes) -> None:
    scoreboard = UsbPhyTxScoreboard()
    accept(scoreboard, data)
    # Arbitrary startup latency and unresolved disabled pins are allowed.
    for cycle in range(7):
        scoreboard.observe(cycle, 0)
    waveform = samples(data)
    observe(scoreboard, waveform, 7)
    scoreboard.observe(7 + len(waveform), 0)
    scoreboard.assert_complete()
    assert scoreboard.observed_frames == 1


@pytest.mark.parametrize("phase", range(4))
def test_rejects_wrong_pin_on_each_sample(phase: int) -> None:
    scoreboard = UsbPhyTxScoreboard()
    data = b"\x80\xd2"
    accept(scoreboard, data)
    waveform = samples(data)
    waveform[4 + phase] = (1, 0, 1)  # Second SYNC bit should be J.
    with pytest.raises(ScoreboardError, match=f"sample={phase + 1}/4"):
        observe(scoreboard, waveform)


@pytest.mark.parametrize("width", (1, 3, 5, 8))
def test_rejects_wrong_bit_duration(width: int) -> None:
    scoreboard = UsbPhyTxScoreboard()
    data = b"\x80\xd2"
    accept(scoreboard, data)
    waveform = samples(data)
    waveform[:4] = [waveform[0]] * width
    with pytest.raises(ScoreboardError, match="expected"):
        observe(scoreboard, waveform)


@pytest.mark.parametrize("mutation", ("missing_stuff", "extra_stuff", "terminal_stuff", "nrzi_inverted", "se1"))
def test_rejects_encoding_errors(mutation: str) -> None:
    data = b"\x80\xfc"  # Six final ones require a stuffed zero after TLAST.
    stream = list(bit_stuff(bits(data)))
    if mutation == "missing_stuff":
        stream = list(bits(data))
    elif mutation == "extra_stuff":
        stream.insert(8, 0)
    elif mutation == "terminal_stuff":
        stream.pop()
    states = list(nrzi_encode(stream))
    if mutation == "nrzi_inverted":
        states = [LineState.K if state is LineState.J else LineState.J for state in states]
    elif mutation == "se1":
        states[5] = LineState.SE1
    states.extend((LineState.SE0, LineState.SE0, LineState.J))
    scoreboard = UsbPhyTxScoreboard()
    accept(scoreboard, data)
    with pytest.raises(ScoreboardError, match="expected"):
        observe(scoreboard, [(1, *state.differential) for state in states for _ in range(4)])


@pytest.mark.parametrize("position", (0, 3, 8, -12, -8, -4, -1))
def test_rejects_oe_dropout(position: int) -> None:
    scoreboard = UsbPhyTxScoreboard()
    data = b"\x80\xd2"
    accept(scoreboard, data)
    waveform = samples(data)
    waveform[position] = (0, 0, 0)
    with pytest.raises(ScoreboardError):
        observe(scoreboard, waveform)
        scoreboard.assert_complete()


@pytest.mark.parametrize("position", (-12, -8, -4))
def test_rejects_corrupt_eop(position: int) -> None:
    scoreboard = UsbPhyTxScoreboard()
    data = b"\x80\xd2"
    accept(scoreboard, data)
    waveform = samples(data)
    waveform[position] = (1, 0, 1)
    with pytest.raises(ScoreboardError, match="EOP"):
        observe(scoreboard, waveform)


def test_extra_drive_missing_output_and_unaccepted_bits() -> None:
    scoreboard = UsbPhyTxScoreboard()
    with pytest.raises(ScoreboardError, match="without accepted input"):
        scoreboard.observe(0, 1, 0, 1)
    accept(scoreboard, b"\x80\xd2")
    with pytest.raises(ScoreboardError, match="missing USB output"):
        scoreboard.assert_complete()
    waveform = samples(b"\x80\xd2")
    observe(scoreboard, waveform, 1)
    scoreboard.assert_complete()
    with pytest.raises(ScoreboardError, match="without accepted input"):
        scoreboard.observe(len(waveform) + 1, 1, 1, 0)


def test_streaming_acceptance_and_packet_state_restart() -> None:
    scoreboard = UsbPhyTxScoreboard()
    cycle = 0
    # The first packet ends in six ones; neither its ones count nor NRZI
    # phase may leak into the next packet, even without an idle sample.
    for data in (b"\x80\xfc", b"\x80\xd2"):
        stream = bits(data)
        waveform = samples(data)
        for offset, sample in enumerate(waveform):
            # New input on every fourth cycle, with no future bits queued.
            if offset % 4 == 0 and offset // 4 < len(stream):
                index = offset // 4
                scoreboard.accept(stream[index], 1, 0, int(index == len(stream) - 1))
            scoreboard.observe(cycle, *sample)
            cycle += 1
    scoreboard.assert_complete()
    assert scoreboard.observed_frames == 2
    assert scoreboard.stuffed_bits == 1


def test_stuffing_inside_packet_while_input_is_stalled() -> None:
    data = b"\x80\xff\xff\xfc"
    stream = bits(data)
    scoreboard = UsbPhyTxScoreboard()
    # Simulate a PHY that accepts every logical bit at its cell boundary,
    # and stalls the source throughout inserted stuffed cells and EOP.
    cycle = 0
    ones = 0
    state = LineState.J
    for index, bit in enumerate(stream):
        scoreboard.accept(bit, 1, 0, int(index == len(stream) - 1))
        if bit == 0:
            state = LineState.K if state is LineState.J else LineState.J
        for _ in range(4):
            scoreboard.observe(cycle, 1, *state.differential)
            cycle += 1
        ones = ones + 1 if bit else 0
        if ones == 6:
            state = LineState.K if state is LineState.J else LineState.J
            for _ in range(4):
                scoreboard.observe(cycle, 1, *state.differential)
                cycle += 1
            ones = 0
    for state in (LineState.SE0, LineState.SE0, LineState.J):
        for _ in range(4):
            scoreboard.observe(cycle, 1, *state.differential)
            cycle += 1
    scoreboard.assert_complete()


def test_terminal_stuffing_packet_has_valid_crc() -> None:
    assert crc16_usb(b"\xfa") == 0xFCC0


def test_nonconsecutive_sampling_is_rejected() -> None:
    scoreboard = UsbPhyTxScoreboard()
    scoreboard.observe(5, 0)
    with pytest.raises(ScoreboardError, match="consecutive"):
        scoreboard.observe(7, 0)
