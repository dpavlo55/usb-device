"""Pure byte-stream scoreboard for the USB packet generator interface."""
from __future__ import annotations

from collections.abc import Iterable

from .usb_fs_codec import encode_packet_bytes
from .usb_fs_types import Packet


class ScoreboardError(AssertionError):
    """A protocol, byte-stream, or completion-contract mismatch."""


class UsbTxScoreboard:
    """Check accepted packet stream bytes: SYNC, PID, payload, and CRC."""

    def __init__(self, packets: Iterable[Packet], context: str = "") -> None:
        # The generator's packet_t* stream includes SYNC before the protocol bytes.
        self._expected = tuple((0x80, *encode_packet_bytes(packet)) for packet in packets)
        self._packet_index = 0
        self._byte_index = 0
        self.context = context
        self.observed: list[int] = []

    @property
    def complete(self) -> bool:
        return self._packet_index == len(self._expected)

    def _error(self, message: str) -> ScoreboardError:
        suffix = f" ({self.context})" if self.context else ""
        return ScoreboardError(message + suffix)

    def _logic(self, value: object, field: str) -> int:
        if value is None:
            raise self._error(f"unresolved {field}")
        resolvable = getattr(value, "is_resolvable", True)
        if callable(resolvable):
            resolvable = resolvable()
        if not resolvable:
            raise self._error(f"unresolved {field}: {value!r}")
        text = str(value).strip().lower()
        if text in {"0", "false"}:
            return 0
        if text in {"1", "true"}:
            return 1
        try:
            number = int(value)
        except (TypeError, ValueError):
            raise self._error(f"unresolved {field}: {value!r}") from None
        if number not in (0, 1):
            raise self._error(f"invalid {field}: {value!r}")
        return number

    def _byte(self, value: object) -> int:
        if value is None:
            raise self._error("unresolved packet_tdata")
        resolvable = getattr(value, "is_resolvable", True)
        if callable(resolvable):
            resolvable = resolvable()
        if not resolvable:
            raise self._error(f"unresolved packet_tdata: {value!r}")
        try:
            if isinstance(value, str):
                text = value.strip().lower()
                if len(text) == 8 and set(text) <= {"0", "1"}:
                    number = int(text, 2)
                else:
                    number = int(text, 0)
            else:
                number = int(value)
        except (TypeError, ValueError):
            raise self._error(f"unresolved packet_tdata: {value!r}") from None
        if not 0 <= number <= 0xFF:
            raise self._error(f"packet_tdata is not a byte: {value!r}")
        return number

    def observe(
        self, packet_tvalid: object, packet_tdata: object, packet_tlast: object
    ) -> None:
        """Observe a packet_t* sample; valid bytes must be accepted AXI transfers."""
        valid = self._logic(packet_tvalid, "packet_tvalid")
        last = self._logic(packet_tlast, "packet_tlast")
        if not valid:
            if last:
                raise self._error("packet_tlast asserted without packet_tvalid")
            return
        if self.complete:
            raise self._error("extra packet_tdata byte after all expected packets")
        value = self._byte(packet_tdata)
        expected = self._expected[self._packet_index][self._byte_index]
        if value != expected:
            raise self._error(
                f"packet_tdata byte mismatch packet={self._packet_index} byte={self._byte_index} "
                f"expected=0x{expected:02X} observed=0x{value:02X}"
            )
        self.observed.append(value)
        final_byte = self._byte_index == len(self._expected[self._packet_index]) - 1
        if last and not final_byte:
            raise self._error(
                "packet_tlast asserted before the final packet byte "
                f"packet={self._packet_index} byte={self._byte_index} packet_tdata=0x{value:02X}"
            )
        if final_byte and not last:
            raise self._error(
                "missing packet_tlast on the final packet byte "
                f"packet={self._packet_index} byte={self._byte_index} "
                f"packet_tdata=0x{value:02X} expected=1 observed=0"
            )
        if final_byte:
            self._packet_index += 1
            self._byte_index = 0
        else:
            self._byte_index += 1

    def assert_complete(self) -> None:
        """Fail if the bounded monitor ended before all bytes were observed."""
        if not self.complete:
            raise self._error(
                f"missing packet_tdata transfer at packet={self._packet_index} byte={self._byte_index}"
            )