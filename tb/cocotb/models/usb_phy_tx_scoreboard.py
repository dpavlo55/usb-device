"""Accepted-bit reference model and four-sample USB TX pin scoreboard."""
from __future__ import annotations

from collections import deque

from .usb_fs_types import LineState
from .usb_tx_scoreboard import ScoreboardError


CLOCKS_PER_BIT = 4
EOP = (LineState.SE0, LineState.SE0, LineState.J)


class UsbPhyTxScoreboard:
    """Check every clock of OE, D+, and D- against accepted serial bits.

    Input includes SYNC and CRC, but no stuffing, NRZI, or EOP. Latency before
    the first driven cell is unrestricted. Once OE rises, every cell lasts
    exactly four consecutive samples, through SE0, SE0, J, then OE falls.
    Pins while OE is low are unspecified. State restarts at each TLAST.
    """

    def __init__(self, context: str = "") -> None:
        self.context = context
        self._pending: deque[tuple[LineState, bool, str]] = deque()
        self._current: tuple[LineState, bool, str] | None = None
        self._sample = 0
        self._previous_cycle: int | None = None
        self._active = False
        self._input_open = False
        self._nrzi = LineState.J
        self._ones = 0
        self.accepted_bits = 0
        self.accepted_frames = 0
        self.observed_frames = 0
        self.observed_cells = 0
        self.stuffed_bits = 0

    @property
    def complete(self) -> bool:
        return not (self._pending or self._current or self._input_open or self._active)

    def _error(self, message: str) -> ScoreboardError:
        return ScoreboardError(f"{message} ({self.context})")

    def _append_bit(self, bit: int, description: str) -> None:
        if not bit:
            self._nrzi = LineState.K if self._nrzi is LineState.J else LineState.J
        self._pending.append((self._nrzi, False, description))

    def accept(self, data: int, keep: int, dest: int, last: int) -> None:
        """Call only at a rising edge with serial_tvalid && serial_tready."""
        if data not in (0, 1) or last not in (0, 1) or keep != 1 or dest != 0:
            raise self._error(f"invalid serial beat data/keep/dest/last={data, keep, dest, last}")
        self._input_open = True
        self._append_bit(data, f"input_bit={self.accepted_bits}")
        self.accepted_bits += 1
        self._ones = self._ones + 1 if data else 0
        if self._ones == 6:
            self._append_bit(0, f"stuffed zero after input_bit={self.accepted_bits - 1}")
            self.stuffed_bits += 1
            self._ones = 0
        if last:
            self._pending.extend(
                (state, index == 2, f"EOP[{index}]") for index, state in enumerate(EOP)
            )
            self.accepted_frames += 1
            self._input_open = False
            self._ones = 0
            self._nrzi = LineState.J

    def observe(self, cycle: int, oe: int, dp: int | None = None, dn: int | None = None) -> None:
        """Call on every clock after HDL updates, including idle clocks."""
        if self._previous_cycle is not None and cycle != self._previous_cycle + 1:
            raise self._error("scoreboard requires consecutive clock samples")
        self._previous_cycle = cycle
        if oe not in (0, 1):
            raise self._error(f"cycle={cycle}: unresolved usb_oe={oe}")
        if not oe:
            if self._active:
                raise self._error(f"cycle={cycle}: usb_oe dropped before the complete EOP")
            return
        if self._current is None:
            if not self._pending:
                raise self._error(f"cycle={cycle}: usb_oe enabled without accepted input")
            self._current = self._pending.popleft()
            self._sample = 0
        state, final, description = self._current
        expected = state.differential
        if (dp, dn) != expected:
            raise self._error(
                f"cycle={cycle} cell={self.observed_cells} sample={self._sample + 1}/4 "
                f"{description}: expected {state.value} D+/D-={expected}, got {(dp, dn)}"
            )
        self._active = True
        self._sample += 1
        if self._sample == CLOCKS_PER_BIT:
            self.observed_cells += 1
            self._current = None
            if final:
                self.observed_frames += 1
                self._active = False

    def assert_complete(self) -> None:
        if not self.complete or self.accepted_frames != self.observed_frames:
            raise self._error(
                f"missing USB output or TLAST: accepted_bits={self.accepted_bits} "
                f"frames={self.observed_frames}/{self.accepted_frames} "
                f"pending_cells={len(self._pending)} partial_cell={self._current is not None}"
            )
