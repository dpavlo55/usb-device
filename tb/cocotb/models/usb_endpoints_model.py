"""Reference model for the endpoint subsystem (``usb_cdc`` + ``usb_endpoints``).

It adds buffer ownership on top of the RAM adapter model. Endpoint ``k`` is
*active* while the core is enabled and ``enable(k)`` is set.

* ``arm(k, length)`` hands the buffer to hardware (``armed(k) = 1``) and
  latches ``length``. Arming does not depend on ``enable(k)``.
* OUT packet for an active OUT endpoint ``k``:
  - armed when the packet starts: the bytes are written from
    ``base_address(k)``; when it ends, ``armed(k)`` is cleared and the event
    ``{k, byte_count, zlp = (byte_count == 0), overrun = 0}`` is sent;
  - not armed: nothing is written and the event
    ``{k, byte_count, zlp = 0, overrun = 1}`` is sent; ``armed(k)`` stays 0.
* OUT packet for any other endpoint: dropped, no event.
* IN request (descriptor ``start``) for an active, armed IN endpoint ``k``
  while no IN packet is in progress: ``length(k)`` bytes are sent from
  ``base_address(k)``; when the last beat leaves, ``armed(k)`` is cleared and
  the event ``{k, 0, 0, 0}`` is sent. Any other request is ignored.
* Split IN transfers: with ``split(k) > 0`` an IN transfer is sent as packets of
  ``P = buffer >> split(k)`` bytes. Each request sends the next
  ``min(remaining, P)`` bytes from ``base_address(k) + sent(k)``; the ACK (or the
  end of the read, isochronous) of a packet shorter than ``P`` or of the one that
  sends the last byte completes the transfer (``sent`` returns to 0, event); any
  other ACK only advances ``sent``. A repeated request without an ACK sends the same
  packet. ``in_last(k)`` tells usb_protocol whether the next packet ends the
  transfer: ``remaining <= P``. With ``split = 0`` every transfer is one packet.
* Buffer size: endpoint ``k`` owns ``2 ** (size(k) + 3)`` bytes from
  ``base_address(k)``. OUT bytes past that are dropped without a write, and an
  IN packet sends at most that many bytes, so no entry touches another's buffer.
* SETUP: the data of a packet routed as SETUP is not written while it arrives.
  Its first 8 bytes are held; only when usb_protocol accepts the SETUP
  (``setup_received``) are they written to the entry's buffer, before the SETUP
  event is raised. A SETUP that is not accepted writes nothing.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .usb_ram_adapter_model import (
    NUM_ENDPOINTS,
    ReadCommand,
    StreamBeat,
    UsbRamAdapterModel,
)

DIRECTION_IN = 0
DIRECTION_OUT = 1
ISOCHRONOUS = 3


@dataclass(frozen=True)
class EndpointConfig:
    base_address: int
    enable: bool
    direction: int
    endpoint_number: int = 0
    kind: int = 0
    size: int = 7
    split: int = 0

    @property
    def buffer_bytes(self) -> int:
        return 8 << self.size

    @property
    def packet_bytes(self) -> int:
        return self.buffer_bytes >> self.split


DISABLED_ENDPOINT = EndpointConfig(base_address=0, enable=False, direction=DIRECTION_IN)


@dataclass(frozen=True)
class TransferEvent:
    endpoint: int
    byte_count: int
    zlp: bool
    overrun: bool
    setup: bool = False


class UsbEndpointsModel:
    def __init__(self, addr_width: int = 10) -> None:
        self.adapter = UsbRamAdapterModel(addr_width)
        self.config: list[EndpointConfig] = [DISABLED_ENDPOINT] * NUM_ENDPOINTS
        self.enable_core = False
        self.armed = [False] * NUM_ENDPOINTS
        self.length = [0] * NUM_ENDPOINTS
        self.sent = [0] * NUM_ENDPOINTS
        self.flight = [0] * NUM_ENDPOINTS
        self._rx_endpoint: int | None = None
        self._rx_accept = True
        self._rx_setup = False
        self._rx_bytes = 0
        self._last_entry = 0
        self._setup_bytes = b""
        self._setup_entry = 0

    @property
    def ram(self) -> bytearray:
        return self.adapter.ram

    def load(self, address: int, data: bytes) -> None:
        self.adapter.load(address, data)

    def configure(self, config: Sequence[EndpointConfig]) -> None:
        if len(config) != NUM_ENDPOINTS:
            raise ValueError(f"expected {NUM_ENDPOINTS} endpoint entries, got {len(config)}")
        if self._rx_endpoint is not None:
            raise ValueError("endpoint configuration changed in the middle of an OUT packet")
        self.config = list(config)
        self.adapter.set_base_addresses([entry.base_address for entry in config])

    def set_enable_core(self, enable: bool) -> None:
        self.enable_core = enable

    def active(self, endpoint: int, direction: int) -> bool:
        entry = self.config[endpoint]
        return self.enable_core and entry.enable and entry.direction == direction

    def arm(self, endpoint: int, length: int) -> None:
        self.armed[endpoint] = True
        self.length[endpoint] = length
        self.sent[endpoint] = 0

    def _total(self, endpoint: int) -> int:
        return min(self.length[endpoint], self.config[endpoint].buffer_bytes)

    def in_last(self, endpoint: int) -> bool:
        """The next IN packet of the armed entry ends its transfer."""
        return self._total(endpoint) - self.sent[endpoint] <= self.config[endpoint].packet_bytes

    def _in_done(self, endpoint: int) -> TransferEvent | None:
        chunk = self.flight[endpoint]
        if chunk < self.config[endpoint].packet_bytes or self.sent[endpoint] + chunk >= self._total(endpoint):
            self.armed[endpoint] = False
            self.sent[endpoint] = 0
            return TransferEvent(endpoint, 0, False, False)
        self.sent[endpoint] += chunk
        return None

    def out_beat(self, beat: StreamBeat, accept: bool = True, setup: bool = False) -> None:
        """Apply one accepted OUT beat to the RAM. Events come from usb_protocol's
        verdict instead (:meth:`out_result`, :meth:`setup_received`).

        ``beat.dest`` is the routed entry (``rx_route.entry``). ``accept`` is
        ``rx_route.accept``; both are taken at the first beat of a packet. A
        packet that is not accepted belongs to another device and writes nothing.
        A SETUP packet (``setup``, from ``rx_route.setup``) is written even while
        the entry is not armed.
        """
        if self._rx_endpoint is None:
            self._rx_endpoint = beat.dest
            self._rx_accept = accept
            self._rx_setup = setup
            self._rx_bytes = 0
            self._last_entry = beat.dest
            if setup:
                self._setup_bytes = b""
                self._setup_entry = beat.dest
            self.adapter.set_write_allowed([
                self.active(k, DIRECTION_OUT) and self.armed[k] for k in range(NUM_ENDPOINTS)
            ])
        if self._rx_accept and self._rx_setup:
            if beat.keep & 1 and len(self._setup_bytes) < 8:
                self._setup_bytes += bytes((beat.data,))
        elif self._rx_accept:
            keep = beat.keep
            if keep & 1:
                if self._rx_bytes >= self.config[self._rx_endpoint].buffer_bytes:
                    keep = 0
                self._rx_bytes += 1
            beat = StreamBeat(data=beat.data, keep=keep, dest=self._rx_endpoint, last=beat.last)
            self.adapter.out_beat(beat)
        if beat.last:
            self._rx_endpoint = None

    def out_result(self, entry: int, byte_count: int, complete: bool) -> TransferEvent:
        """usb_protocol's verdict on an OUT packet: ``complete`` hands the buffer back
        with ``byte_count`` bytes, otherwise the entry was not armed (overrun)."""
        if complete:
            self.armed[entry] = False
            return TransferEvent(entry, byte_count, byte_count == 0, False)
        return TransferEvent(entry, 0, False, True)

    def setup_received(self) -> TransferEvent:
        """usb_protocol accepted the SETUP just received: its held bytes (at most 8)
        are written to the entry's buffer, both entries serving that endpoint number
        lose their arm, and the entry reports 8 SETUP bytes."""
        entry = self._last_entry
        if self.active(self._setup_entry, DIRECTION_OUT):
            base = self.config[self._setup_entry].base_address
            for index, byte in enumerate(self._setup_bytes):
                self.adapter.ram[(base + index) % len(self.adapter.ram)] = byte
        number = self.config[entry].endpoint_number
        for k in range(NUM_ENDPOINTS):
            if self.config[k].endpoint_number == number:
                self.armed[k] = False
        return TransferEvent(entry, 8, False, False, True)

    def in_request(self, endpoint: int) -> tuple[tuple[StreamBeat, ...], TransferEvent | None] | None:
        """Expected IN beats for a request, or ``None`` if it is ignored.

        The entry stays armed until :meth:`in_result` (the host's ACK); an
        isochronous entry expects no ACK and completes at the end of the read,
        so its event comes with the beats."""
        if not (self.active(endpoint, DIRECTION_IN) and self.armed[endpoint]):
            return None
        config = self.config[endpoint]
        chunk = min(self._total(endpoint) - self.sent[endpoint], config.packet_bytes)
        self.flight[endpoint] = chunk
        address = (config.base_address + self.sent[endpoint]) % len(self.adapter.ram)
        beats = self.adapter.in_packet(ReadCommand(endpoint, address, chunk))
        if config.kind == ISOCHRONOUS:
            return beats, self._in_done(endpoint)
        return beats, None

    def in_result(self, entry: int) -> TransferEvent | None:
        """usb_protocol saw the host's ACK: the transfer goes on with its next packet,
        or the buffer is handed back."""
        return self._in_done(entry)
