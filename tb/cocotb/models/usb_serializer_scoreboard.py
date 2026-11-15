"""Accepted-byte reference model and cycle-accurate USB serializer scoreboard."""
from __future__ import annotations

from collections import deque

from .usb_fs_types import LineState
from .usb_tx_scoreboard import ScoreboardError


EOP = (LineState.SE0, LineState.SE0, LineState.J)


def logic_value(value: object, name: str) -> int:
    """Reject unresolved simulator values before converting them to integers."""
    if not getattr(value, "is_resolvable", True):
        raise ScoreboardError(f"unresolved {name}: {value}")
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ScoreboardError(f"unresolved {name}: {value}") from None


def line_state_value(value: object) -> LineState:
    """Read cocotb's VHDL enum position in usb_pkg.usb_line_state_t order."""
    states = (LineState.J, LineState.K, LineState.SE0, LineState.SE1)
    position = logic_value(value, "usb_line_state")
    if not 0 <= position < len(states):
        raise ScoreboardError(f"invalid usb_line_state={position} (USB_LINE_INVALID or unknown enum)")
    return states[position]


class UsbSerializerScoreboard:
    """Queue symbols only from accepted AXI beats, including null-beat TLAST.

    SYNC is already in the input stream. Stuffing and NRZI state span every
    kept byte, but restart at each packet. Output latency is unrestricted;
    once a packet starts, every symbol through the EOP J must appear on
    consecutive clocks. Invalid-cycle line state is deliberately ignored.
    """

    def __init__(self, context: str = "") -> None:
        self.context = context
        self._expected: deque[tuple[LineState, bool, str]] = deque()
        self._nrzi = LineState.J
        self._ones = 0
        self._input_open = False
        self._active = False
        self.last_valid_cycle: int | None = None
        self.accepted_beats = 0
        self.accepted_frames = 0
        self.observed_frames = 0
        self.observed_symbols = 0
        self.stuffed_bits = 0
        self.last_state: LineState | None = None

    @property
    def pending_symbols(self) -> int:
        return len(self._expected)

    @property
    def complete(self) -> bool:
        return not self._expected and not self._input_open and not self._active

    def _error(self, message: str) -> ScoreboardError:
        context = self._expected[0][2] if self._expected else self.context
        return ScoreboardError(f"{message} ({context})" if context else message)

    def _append_bit(self, bit: int) -> None:
        if not bit:
            self._nrzi = LineState.K if self._nrzi is LineState.J else LineState.J
        self._expected.append((self._nrzi, False, self.context))

    def accept(self, data: int, keep: int, last: int) -> None:
        """Observe exactly one input TVALID/TREADY handshake at the clock edge."""
        if not 0 <= data <= 0xFF or keep not in (0, 1) or last not in (0, 1):
            raise ValueError("expected an eight-bit AXI beat with binary KEEP/LAST")
        self.accepted_beats += 1
        self._input_open = True
        if keep:
            for index in range(8):
                bit = (data >> index) & 1
                self._append_bit(bit)
                self._ones = self._ones + 1 if bit else 0
                if self._ones == 6:
                    self._append_bit(0)
                    self.stuffed_bits += 1
                    self._ones = 0
        if last:
            self._expected.extend(
                (state, index == 2, self.context) for index, state in enumerate(EOP)
            )
            self.accepted_frames += 1
            self._input_open = False
            self._nrzi = LineState.J
            self._ones = 0

    def observe(self, cycle: int, valid: int, state: LineState | None = None) -> None:
        """Observe settled output on every clock, not just clocks with VALID."""
        if valid not in (0, 1):
            raise self._error(f"cycle={cycle}: invalid usb_line_state_valid={valid}")
        if self._active:
            assert self.last_valid_cycle is not None
            interval = cycle - self.last_valid_cycle
            if interval != 1 or not valid:
                raise self._error(
                    f"cycle={cycle}: USB sample cadence must be every clock through EOP; "
                    f"interval={interval} valid={valid}"
                )
        if not valid:
            return
        if not self._expected:
            raise self._error(f"cycle={cycle}: unexpected USB symbol without accepted input")
        expected, final, _ = self._expected[0]
        if state is not expected:
            raise self._error(
                f"cycle={cycle}: USB symbol mismatch frame={self.observed_frames} "
                f"symbol={self.observed_symbols} expected={expected} observed={state}"
            )
        self._expected.popleft()
        self.last_valid_cycle = cycle
        self.last_state = state
        self.observed_symbols += 1
        self._active = not final
        if final:
            self.observed_frames += 1

    def assert_complete(self) -> None:
        if not self.complete:
            raise self._error(
                f"missing USB output or TLAST: pending_symbols={self.pending_symbols} "
                f"input_open={self._input_open} frames={self.observed_frames}/"
                f"{self.accepted_frames}"
            )
