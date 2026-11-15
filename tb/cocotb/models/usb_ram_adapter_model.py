"""Reference model and scoreboard for the USB-side shared RAM adapter.

The model only encodes the agreed specification; it knows nothing about the
RTL structure:

* OUT (``rx_payload``): a packet is the beats up to and including ``tlast``.
  The first beat's ``tdest`` selects endpoint ``k``. ``base_address(k)`` and
  ``write_allowed(k)`` are sampled when the first beat is taken; later changes
  do not affect the packet. If writing is allowed, byte ``i`` of the packet
  (beats with ``tkeep(0) = 1``) is written to ``base_address(k) + i``.
  Otherwise the packet is consumed and dropped. A single beat with
  ``tkeep = 0`` and ``tlast = 1`` is a zero-length packet. Every packet gives
  one OUT report ``{k, byte_count, accepted}``, in the cycle its last beat is
  taken, where ``byte_count`` counts the ``tkeep(0) = 1`` beats.
* IN (``read_command``): a ``start`` produces ``length`` beats read from
  ``base_address`` onwards, with ``tdest = endpoint``, ``tkeep = 1`` and
  ``tlast`` on the last beat. ``length = 0`` produces one beat with
  ``tkeep = 0`` and ``tlast = 1``. The IN report ``{endpoint, length, 1}``
  appears in the cycle the last beat is taken on ``tx_payload``.
"""
from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

NUM_ENDPOINTS = 16


class ScoreboardError(AssertionError):
    """A mismatch between the observed DUT behaviour and the reference model."""


@dataclass(frozen=True)
class StreamBeat:
    """One AXI4-Stream transfer. ``data`` is ``None`` when it is don't-care."""

    data: int | None
    keep: int
    dest: int
    last: int


@dataclass(frozen=True)
class PacketReport:
    endpoint: int
    byte_count: int
    accepted: bool


@dataclass(frozen=True)
class ReadCommand:
    endpoint: int
    base_address: int
    length: int


def out_packet_beats(dest: int, payload: bytes) -> tuple[StreamBeat, ...]:
    """Beats a well-behaved upstream sends for one OUT packet."""
    if not payload:
        return (StreamBeat(data=0, keep=0, dest=dest, last=1),)
    return tuple(
        StreamBeat(data=byte, keep=1, dest=dest, last=int(index == len(payload) - 1))
        for index, byte in enumerate(payload)
    )


class UsbRamAdapterModel:
    """Shared RAM contents plus the per-endpoint base address and write permission."""

    def __init__(self, addr_width: int = 10) -> None:
        self.ram_size = 1 << addr_width
        self.ram = bytearray(self.ram_size)
        self.base_address = [0] * NUM_ENDPOINTS
        self.write_allowed = [False] * NUM_ENDPOINTS
        self._rx_endpoint: int | None = None
        self._rx_base = 0
        self._rx_allowed = False
        self._rx_count = 0

    @property
    def rx_idle(self) -> bool:
        return self._rx_endpoint is None

    def set_base_addresses(self, base_address: Sequence[int]) -> None:
        if len(base_address) != NUM_ENDPOINTS:
            raise ValueError(f"expected {NUM_ENDPOINTS} base addresses, got {len(base_address)}")
        for address in base_address:
            if not 0 <= address < self.ram_size:
                raise ValueError(f"base address {address:#x} outside the RAM")
        self.base_address = list(base_address)

    def set_write_allowed(self, write_allowed: Sequence[bool]) -> None:
        if len(write_allowed) != NUM_ENDPOINTS:
            raise ValueError(f"expected {NUM_ENDPOINTS} write permissions, got {len(write_allowed)}")
        self.write_allowed = [bool(allowed) for allowed in write_allowed]

    def load(self, address: int, data: bytes) -> None:
        for offset, byte in enumerate(data):
            self.ram[(address + offset) % self.ram_size] = byte

    def out_beat(self, beat: StreamBeat) -> PacketReport | None:
        """Apply one OUT beat that the DUT accepted; returns the report on ``tlast``."""
        if self._rx_endpoint is None:
            self._rx_endpoint = beat.dest
            self._rx_base = self.base_address[beat.dest]
            self._rx_allowed = self.write_allowed[beat.dest]
            self._rx_count = 0
        if beat.keep & 1:
            if self._rx_allowed:
                self.ram[(self._rx_base + self._rx_count) % self.ram_size] = beat.data
            self._rx_count += 1
        if not beat.last:
            return None
        report = PacketReport(self._rx_endpoint, self._rx_count % 1024, self._rx_allowed)
        self._rx_endpoint = None
        return report

    def in_packet(self, command: ReadCommand) -> tuple[StreamBeat, ...]:
        """Expected IN beats for a read command."""
        if command.length == 0:
            return (StreamBeat(data=None, keep=0, dest=command.endpoint, last=1),)
        return tuple(
            StreamBeat(
                data=self.ram[(command.base_address + index) % self.ram_size],
                keep=1,
                dest=command.endpoint,
                last=int(index == command.length - 1),
            )
            for index in range(command.length)
        )

    @staticmethod
    def in_report(command: ReadCommand) -> PacketReport:
        return PacketReport(command.endpoint, command.length, True)


class TxScoreboard:
    """Compare accepted ``tx_payload`` beats with the expected IN packets.

    Errors are collected rather than raised so a run reports every problem; a
    packet that goes out of sync is abandoned after its first error.
    """

    def __init__(self, context: str = "") -> None:
        self.context = context
        self.errors: list[str] = []
        self.completed_packets = 0
        self._expected: deque[tuple[int, tuple[StreamBeat, ...]]] = deque()
        self._packet_id = 0
        self._beat_index = 0
        self._discarding = False

    def _error(self, message: str) -> None:
        suffix = f" ({self.context})" if self.context else ""
        self.errors.append(message + suffix)

    def expect(self, beats: Iterable[StreamBeat]) -> None:
        self._expected.append((self._packet_id, tuple(beats)))
        self._packet_id += 1

    @property
    def outstanding(self) -> int:
        return len(self._expected)

    def observe(self, beat: StreamBeat) -> None:
        if not self._expected:
            self._error(f"unexpected tx beat {beat}")
            return
        packet_id, beats = self._expected[0]
        expected = beats[self._beat_index]
        if not self._discarding:
            mismatch = (
                beat.keep != expected.keep
                or beat.dest != expected.dest
                or beat.last != expected.last
                or (expected.keep & 1 and beat.data != expected.data)
            )
            if mismatch:
                self._error(
                    f"IN packet {packet_id} beat {self._beat_index}: "
                    f"expected {expected}, observed {beat}"
                )
                self._discarding = True
        self._beat_index += 1
        if beat.last or self._beat_index == len(beats):
            if not beat.last and not self._discarding:
                self._error(f"IN packet {packet_id}: tlast missing on beat {self._beat_index - 1}")
            self._expected.popleft()
            self._beat_index = 0
            self._discarding = False
            self.completed_packets += 1

    def check_complete(self) -> None:
        for packet_id, beats in self._expected:
            self._error(f"IN packet {packet_id} ({len(beats)} beats) was not fully received")
        self._expected.clear()


class InOrderScoreboard:
    """Compare a sequence of observed items (reports, events) with expected ones, in order.

    Either side may run ahead: an item observed before the model has predicted
    it waits until the prediction arrives.
    """

    def __init__(self, what: str, context: str = "") -> None:
        self.what = what
        self.context = context
        self.errors: list[str] = []
        self.observed = 0
        self.matched = 0
        self._expected: deque = deque()
        self._observed: deque = deque()

    def _error(self, message: str) -> None:
        suffix = f" ({self.context})" if self.context else ""
        self.errors.append(message + suffix)

    def _match(self) -> None:
        while self._expected and self._observed:
            expected = self._expected.popleft()
            observed = self._observed.popleft()
            if observed != expected:
                self._error(f"{self.what} {self.matched}: expected {expected}, observed {observed}")
            self.matched += 1

    def expect(self, item) -> None:
        self._expected.append(item)
        self._match()

    def observe(self, item) -> None:
        self.observed += 1
        self._observed.append(item)
        self._match()

    @property
    def outstanding(self) -> int:
        """Predicted items not observed yet."""
        return len(self._expected)

    def check_complete(self) -> None:
        for item in self._expected:
            self._error(f"missing {self.what}: {item}")
        for item in self._observed:
            self._error(f"unexpected {self.what}: {item}")
        self._expected.clear()
        self._observed.clear()


def compare_ram(expected: bytes, observed: bytes, context: str = "") -> list[str]:
    """Describe every differing RAM byte (first 16 listed in full)."""
    suffix = f" ({context})" if context else ""
    if len(expected) != len(observed):
        return [f"RAM size mismatch: expected {len(expected)}, observed {len(observed)}{suffix}"]
    differences = [
        address for address, (want, got) in enumerate(zip(expected, observed)) if want != got
    ]
    messages = [
        f"RAM[{address:#05x}]: expected {expected[address]:#04x}, "
        f"observed {observed[address]:#04x}{suffix}"
        for address in differences[:16]
    ]
    if len(differences) > 16:
        messages.append(f"... and {len(differences) - 16} more differing RAM bytes{suffix}")
    return messages
