"""From packets to transactions to control transfers, as a passive observer sees them.

:class:`TransactionAssembler` groups token, data and handshake packets into
:class:`ObservedTransaction` objects; :class:`ControlTransferAssembler` groups those into
:class:`ObservedControlTransfer` objects (SETUP, data stage, status stage). Both are plain
Python; :class:`UsbTransferMonitor` wraps them as a UVM subscriber of the bus monitor.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field

from pyuvm import uvm_analysis_port, uvm_subscriber

from ..ch9.constants import Direction
from ..common.tlm import analysis_imp
from ..ch9.setup import SetupPacket
from .line import LineEvent, ObservedPacket, Source
from .wire import DataPacket, HandshakePacket, Pid, SofPacket, TokenPacket, fs_to_bits


@dataclass
class ObservedTransaction:
    token: TokenPacket
    start_fs: int
    token_end_fs: int
    data: DataPacket | None = None
    data_source: Source | None = None
    data_ok: bool = True
    handshake: Pid | None = None
    handshake_source: Source | None = None
    response_delay_bits: float | None = None
    """Time from the end of the host's last packet to the device's response."""

    @property
    def kind(self) -> Pid:
        return self.token.pid

    @property
    def address(self) -> int:
        return self.token.address

    @property
    def endpoint(self) -> int:
        return self.token.endpoint

    @property
    def device_responded(self) -> bool:
        return self.handshake_source is Source.DEVICE or self.data_source is Source.DEVICE

    @property
    def complete(self) -> bool:
        """Data accepted: ACKed OUT/SETUP, or IN data the host ACKed."""
        return self.handshake is Pid.ACK

    def __str__(self) -> str:
        data = f" {self.data.pid.name}[{len(self.data.payload)}]" if self.data else ""
        hs = f" {self.handshake.name}" if self.handshake else " (no handshake)"
        return f"{self.kind.name} {self.address}.{self.endpoint}{data}{hs}"


class TransactionAssembler:
    """Packets in, transactions out (each emitted when it is complete or superseded)."""

    def __init__(self) -> None:
        self.current: ObservedTransaction | None = None
        self._last_host_end_fs = 0

    def feed(self, observed: ObservedPacket) -> list[ObservedTransaction]:
        packet, done = observed.packet, []
        if observed.source is Source.HOST:
            self._last_host_end_fs = observed.end_fs
        if isinstance(packet, TokenPacket) and observed.source is Source.HOST:
            done += self.flush()
            self.current = ObservedTransaction(packet, observed.start_fs, observed.end_fs)
            return done
        if isinstance(packet, SofPacket):
            return self.flush()
        txn = self.current
        if txn is None:
            return done
        if observed.source is Source.DEVICE and txn.response_delay_bits is None:
            txn.response_delay_bits = fs_to_bits(observed.start_fs - self._last_host_end_fs)
        if not observed.ok:
            if txn.data is None:
                txn.data_ok = False
                txn.data_source = observed.source
            return done
        if isinstance(packet, DataPacket):
            txn.data, txn.data_source = packet, observed.source
        elif isinstance(packet, HandshakePacket):
            txn.handshake, txn.handshake_source = packet.pid, observed.source
            if txn.kind is not Pid.IN or packet.pid is Pid.ACK or txn.data is None:
                done += self.flush()
        return done

    def flush(self) -> list[ObservedTransaction]:
        txn, self.current = self.current, None
        return [txn] if txn else []


class ControlOutcome(enum.Enum):
    COMPLETED = "completed"
    STALLED = "stalled"
    ABORTED = "aborted"


@dataclass
class ObservedControlTransfer:
    address: int
    setup: SetupPacket
    start_fs: int
    data: bytes = b""
    data_packets: int = 0
    data_pids: list[Pid] = field(default_factory=list)
    status_pid: Pid | None = None
    naks: int = 0
    outcome: ControlOutcome = ControlOutcome.ABORTED
    stalled_in: str = ""
    end_fs: int = 0

    @property
    def succeeded(self) -> bool:
        return self.outcome is ControlOutcome.COMPLETED

    def __str__(self) -> str:
        detail = f", {len(self.data)} data bytes" if self.data else ""
        stage = f" in {self.stalled_in} stage" if self.stalled_in else ""
        return f"control @{self.address}: {self.setup} -> {self.outcome.value}{stage}{detail}"


class ControlTransferAssembler:
    """Transactions on endpoint 0 in, control transfers out."""

    def __init__(self) -> None:
        self.current: ObservedControlTransfer | None = None
        self._in_status = False

    def feed(self, txn: ObservedTransaction) -> list[ObservedControlTransfer]:
        if txn.endpoint != 0:
            return []
        if txn.kind is Pid.SETUP:
            done = self._finish(ControlOutcome.ABORTED, txn.start_fs)
            if txn.complete and txn.data is not None and len(txn.data.payload) == 8:
                self.current = ObservedControlTransfer(txn.address, SetupPacket.from_bytes(txn.data.payload),
                                                       txn.start_fs)
                self._in_status = False
            return done
        transfer = self.current
        if transfer is None or txn.address != transfer.address:
            return []
        if txn.handshake is Pid.NAK:
            transfer.naks += 1
            return []
        if txn.handshake is Pid.STALL:
            transfer.stalled_in = "status" if self._is_status(txn) else "data"
            return self._finish(ControlOutcome.STALLED, txn.start_fs)
        if not txn.complete:
            return []
        if self._is_status(txn):
            transfer.status_pid = txn.data.pid if txn.data else None
            return self._finish(ControlOutcome.COMPLETED, txn.start_fs)
        transfer.data += txn.data.payload if txn.data else b""
        transfer.data_packets += 1
        if txn.data:
            transfer.data_pids.append(txn.data.pid)
        return []

    def _is_status(self, txn: ObservedTransaction) -> bool:
        """The status stage runs opposite to the data stage (IN when there is no data stage)."""
        setup = self.current.setup
        data_in = setup.direction is Direction.IN and setup.w_length > 0
        status_kind = Pid.OUT if data_in else Pid.IN
        return txn.kind is status_kind

    def _finish(self, outcome: ControlOutcome, time_fs: int) -> list[ObservedControlTransfer]:
        transfer, self.current = self.current, None
        if transfer is None:
            return []
        transfer.outcome = outcome
        transfer.end_fs = time_fs
        return [transfer]

    def abort(self, time_fs: int) -> list[ObservedControlTransfer]:
        return self._finish(ControlOutcome.ABORTED, time_fs)


class UsbTransferMonitor(uvm_subscriber):
    """Subscribes to the bus monitor's packets; publishes transactions and control transfers.

    Line events arrive on ``line_event_export``: a bus reset aborts the control transfer
    in progress.
    """

    def build_phase(self):
        self.line_event_export = analysis_imp("line_event_export", self, self.line_event)
        self.transaction_ap = uvm_analysis_port("transaction_ap", self)
        self.control_ap = uvm_analysis_port("control_ap", self)
        self._transactions = TransactionAssembler()
        self._controls = ControlTransferAssembler()

    def write(self, observed: ObservedPacket) -> None:
        for txn in self._transactions.feed(observed):
            self._publish(txn)

    def line_event(self, event: LineEvent) -> None:
        for txn in self._transactions.flush():
            self._publish(txn)
        for transfer in self._controls.abort(event.start_fs):
            self.control_ap.write(transfer)

    def _publish(self, txn: ObservedTransaction) -> None:
        self.logger.debug("%s", txn)
        self.transaction_ap.write(txn)
        for transfer in self._controls.feed(txn):
            self.logger.info("%s", transfer)
            self.control_ap.write(transfer)
