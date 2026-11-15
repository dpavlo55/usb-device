"""The hardware abstraction layer the device software runs on.

:class:`DeviceHal` is the contract of the device core as software sees it, the same one
``rdl/usb_device.rdl`` documents: endpoint entries with buffers, arm/complete ownership,
stall, data-toggle reset, SETUP capture, device address, pull-up, bus events and remote
wakeup. Two implementations exist: :class:`~usbtb.device.register_hal.RegisterHal` works
the real registers through the RAL, :class:`~usbtb.device.reference_hal.ReferenceHal` is a
behavioral model of the core for checking the testbench itself.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from ..ch9.constants import Direction, TransferType

EP0_OUT = 0
EP0_IN = 1

RDL_ENDPOINT_KIND = {
    TransferType.CONTROL: 0,
    TransferType.BULK: 1,
    TransferType.INTERRUPT: 2,
    TransferType.ISOCHRONOUS: 3,
}
"""``endpoint[n].control.kind`` values (``endpoint_kind_e`` in rdl/usb_device.rdl). They
differ from the descriptor encoding of :class:`TransferType`; the generated register
model carries no enum values, so a unit test checks this table against the RDL."""
EP0_BUFFER_BYTES = 64
ENTRIES = 16


@dataclass(frozen=True, slots=True)
class EntryConfig:
    """One endpoint entry: which USB endpoint it serves and where its buffer lives."""

    endpoint_number: int
    direction: Direction
    kind: TransferType
    base_address: int
    buffer_bytes: int
    enable: bool = True

    @property
    def size_code(self) -> int:
        """The ``control.size`` encoding: bytes = 2 ** (size + 3)."""
        return self.buffer_bytes.bit_length() - 4


# Events reported by the hardware ------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class SetupReceived:
    data: bytes


@dataclass(frozen=True, slots=True)
class TransferDone:
    entry: int
    data: bytes = b""
    """For OUT entries, the bytes received; empty for IN entries."""


@dataclass(frozen=True, slots=True)
class BusReset:
    pass


@dataclass(frozen=True, slots=True)
class Suspended:
    pass


@dataclass(frozen=True, slots=True)
class Resumed:
    pass


HalEvent = SetupReceived | TransferDone | BusReset | Suspended | Resumed


class DeviceHal(ABC):
    @abstractmethod
    async def start(self) -> None:
        """Enable the core and its interrupts; nothing is attached yet."""

    def stop(self) -> None:
        """Cancel background activity (called when reset asserts)."""

    @abstractmethod
    async def next_event(self) -> HalEvent:
        """Wait for the next hardware event."""

    @abstractmethod
    async def configure_entry(self, entry: int, config: EntryConfig) -> None:
        """Program an entry (stall cleared, toggle back to DATA0)."""

    @abstractmethod
    async def disable_entry(self, entry: int) -> None: ...

    @abstractmethod
    async def arm_in(self, entry: int, data: bytes) -> None:
        """Copy ``data`` into the entry's buffer and hand it to hardware."""

    @abstractmethod
    async def arm_out(self, entry: int) -> None: ...

    @abstractmethod
    async def set_stall(self, entry: int, stalled: bool) -> None: ...

    @abstractmethod
    async def reset_toggle(self, entry: int) -> None: ...

    @abstractmethod
    async def set_address(self, address: int) -> None: ...

    @abstractmethod
    async def set_pull_up(self, enabled: bool) -> None: ...

    @abstractmethod
    async def signal_remote_wakeup(self) -> None: ...
