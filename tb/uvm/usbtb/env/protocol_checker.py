"""Packet-level protocol rules for the device (USB 2.0 chapter 8 and section 7.1.18).

* every device packet decodes cleanly (PID check, CRC, bit stuffing, EOP);
* the device transmits only when a host packet asks for an answer;
* the answer is legal for the token: SETUP gets ACK only (never NAK/STALL, section 8.5.3),
  OUT gets ACK/NAK/STALL, IN gets DATA0/DATA1/NAK/STALL;
* the device answers within the turnaround time;
* on endpoint 0, IN data toggles start at DATA1 after each SETUP and alternate.
"""
from __future__ import annotations

from pyuvm import ConfigDB, uvm_component

from ..common.compliance import ComplianceLog
from ..common.reset import ResetAware
from ..common.tlm import analysis_imp
from ..usb.line import ObservedPacket, Source
from ..usb.timing import UsbTiming
from ..usb.transfers import ObservedTransaction
from ..usb.wire import DATA_PIDS, HANDSHAKE_PIDS, DataPacket, Pid, TokenPacket, other_data_pid

SOURCE = "protocol"
LEGAL_ANSWERS = {
    Pid.SETUP: {Pid.ACK},
    Pid.OUT: set(HANDSHAKE_PIDS),
    Pid.IN: set(DATA_PIDS) | {Pid.NAK, Pid.STALL},
}


class UsbProtocolChecker(ResetAware, uvm_component):
    def build_phase(self):
        super().build_phase()
        self.log: ComplianceLog = ConfigDB().get(self, "", "compliance")
        self.timing: UsbTiming = ConfigDB().get(self, "", "timing")
        self.packet_export = analysis_imp("packet_export", self, self.write_packet)
        self.transaction_export = analysis_imp("transaction_export", self, self.write_transaction)
        self.device_packets = 0
        self.on_reset()

    def on_reset(self) -> None:
        self._asked: Pid | None = None
        self._data_token: Pid | None = None
        self._ep0_in_toggle = Pid.DATA1

    def write_packet(self, observed: ObservedPacket) -> None:
        if observed.source is Source.HOST:
            self._track_host(observed)
            return
        self.device_packets += 1
        if not self.log.check(observed.ok, SOURCE, f"device sent a corrupt packet: {', '.join(observed.errors)}"):
            self._asked = None
            return
        asked, self._asked = self._asked, None
        if not self.log.check(asked is not None, SOURCE, f"device sent {observed.packet} without being asked"):
            return
        self.log.check(observed.pid in LEGAL_ANSWERS[asked], SOURCE,
                       f"device answered {asked.name} with {observed.pid.name}")

    def _track_host(self, observed: ObservedPacket) -> None:
        """Which answer the bus waits for after this host packet (``None``: no answer)."""
        self._asked = None
        match observed.packet:
            case TokenPacket(pid=Pid.IN):
                self._asked, self._data_token = Pid.IN, None
            case TokenPacket(pid=pid) if pid in (Pid.SETUP, Pid.OUT):
                self._data_token = pid  # the device answers after the data packet
            case DataPacket() if self._data_token is not None:
                self._asked, self._data_token = self._data_token, None
            case _:
                self._data_token = None

    def write_transaction(self, txn: ObservedTransaction) -> None:
        if txn.response_delay_bits is not None:
            self.log.check(txn.response_delay_bits <= self.timing.max_device_response_bits, SOURCE,
                           f"{txn}: device answered after {txn.response_delay_bits:.1f} bit times "
                           f"(limit {self.timing.max_device_response_bits})")
        if txn.endpoint != 0:
            return
        if txn.kind is Pid.SETUP and txn.complete:
            self._ep0_in_toggle = Pid.DATA1
        elif txn.kind is Pid.IN and txn.complete and txn.data is not None:
            self.log.check(txn.data.pid is self._ep0_in_toggle, SOURCE,
                           f"{txn}: endpoint 0 sent {txn.data.pid.name}, expected {self._ep0_in_toggle.name}")
            self._ep0_in_toggle = other_data_pid(txn.data.pid)

    def report_phase(self):
        self.logger.info("protocol checker: %d device packets checked", self.device_packets)

