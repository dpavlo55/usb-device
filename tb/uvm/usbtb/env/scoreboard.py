"""The scoreboard: does the hardware do what the device software asked?

Expectations come from the software's intents (:mod:`usbtb.device.intent`), observations
from the bus. Checked here:

* IN data on the wire equals the bytes the software armed, in order, per endpoint;
* OUT data the software reads back equals what the device ACKed on the wire (endpoint 0);
* the SETUP bytes the software reads equal the last SETUP the device ACKed;
* a stalled endpoint answers STALL, an endpoint that is not stalled never does;
* the device answers only tokens for its own address.
"""
from __future__ import annotations

from collections import defaultdict, deque

from pyuvm import ConfigDB, uvm_component

from ..ch9.constants import Direction
from ..common.compliance import ComplianceLog
from ..common.reset import ResetAware
from ..common.tlm import analysis_imp
from ..device.intent import AddressChanged, InDataArmed, Intent, OutDataReceived, SetupRead, StallChanged
from ..usb.line import LineCondition, LineEvent, Source
from ..usb.transfers import ObservedTransaction
from ..usb.wire import Pid, other_data_pid

SOURCE = "scoreboard"


class UsbScoreboard(ResetAware, uvm_component):
    def build_phase(self):
        super().build_phase()
        self.log: ComplianceLog = ConfigDB().get(self, "", "compliance")
        self.intent_export = analysis_imp("intent_export", self, self.write_intent)
        self.transaction_export = analysis_imp("transaction_export", self, self.write_transaction)
        self.line_event_export = analysis_imp("line_event_export", self, self.write_line_event)
        self.matched = 0
        self.on_reset()

    def on_reset(self) -> None:
        """A reset of the device: nothing armed, nothing stalled, address 0."""
        self.in_expected: dict[int, deque[bytes]] = defaultdict(deque)
        self.out_accepted: deque[bytes] = deque()
        self.out_read: deque[bytes] = deque()
        self.out_toggle = Pid.DATA0
        self.setups_acked: deque[bytes] = deque()
        self.setups_read: deque[bytes] = deque()
        self.stall_requested: set[tuple[int, Direction]] = set()
        self.stall_active: set[tuple[int, Direction]] = set()
        self.addresses = {0}
        self.latest_address = 0

    # Intents -----------------------------------------------------------------------------------

    def write_intent(self, intent: Intent) -> None:
        match intent:
            case InDataArmed(endpoint=endpoint, data=data):
                self.in_expected[endpoint].append(data)
            case OutDataReceived(endpoint=endpoint, data=data):
                self._check_out(endpoint, data)
            case SetupRead(data=data):
                # The software abandons the previous control transfer; hardware has cleared
                # the endpoint 0 stall (the software may stall again for this request).
                self.in_expected[0].clear()
                for direction in Direction:
                    self.stall_requested.discard((0, direction))
                    self.stall_active.discard((0, direction))
                self.setups_read.append(data)
                self._match_setups()
            case StallChanged(endpoint=endpoint, direction=direction, stalled=True):
                self.stall_requested.add((endpoint, direction))
            case StallChanged(endpoint=endpoint, direction=direction, stalled=False):
                self.stall_requested.discard((endpoint, direction))
                self.stall_active.discard((endpoint, direction))
            case AddressChanged(address=address):
                self.addresses.add(address)
                self.latest_address = address

    def _check_out(self, endpoint: int, data: bytes) -> None:
        if endpoint == 0:
            self.out_read.append(data)
            self._match_out()

    def _match_out(self) -> None:
        """OUT data on endpoint 0, matched in order: software can see its completion before
        the bus monitor reports the device's ACK."""
        while self.out_accepted and self.out_read:
            wire, read = self.out_accepted.popleft(), self.out_read.popleft()
            if self.log.check(read == wire, SOURCE, f"OUT data on endpoint 0: buffer holds {read.hex()}, "
                                                    f"the wire carried {wire.hex()}"):
                self.matched += 1

    # Bus -------------------------------------------------------------------------------------

    def write_line_event(self, event: LineEvent) -> None:
        if event.condition in (LineCondition.RESET, LineCondition.DETACH):
            self.stall_requested = {k for k in self.stall_requested if k[0] != 0}
            self.stall_active = {k for k in self.stall_active if k[0] != 0}
            self.in_expected.clear()

    def write_transaction(self, txn: ObservedTransaction) -> None:
        if txn.device_responded:
            self._check_address(txn)
        if txn.kind is Pid.SETUP:
            self._on_setup(txn)
            return
        direction = Direction.IN if txn.kind is Pid.IN else Direction.OUT
        self._check_stall(txn, (txn.endpoint, direction))
        if txn.kind is Pid.IN and txn.data_source is Source.DEVICE and txn.data is not None:
            self._check_in(txn)
        elif txn.kind is Pid.OUT and txn.endpoint == 0 and txn.handshake is Pid.ACK and txn.data:
            if txn.data.pid is self.out_toggle:
                self.out_accepted.append(txn.data.payload)
                self.out_toggle = other_data_pid(self.out_toggle)
                self._match_out()

    def _match_setups(self) -> None:
        """Software may read a SETUP before the bus monitor reports its handshake, so the
        two sides are matched in order, whichever arrives first."""
        while self.setups_acked and self.setups_read:
            if self.setups_acked.popleft() == self.setups_read[0]:
                self.setups_read.popleft()
                self.matched += 1
            # Otherwise a newer SETUP replaced that one before software read it: skip it.

    def _on_setup(self, txn: ObservedTransaction) -> None:
        if txn.handshake is Pid.ACK and txn.data is not None:
            self.setups_acked.append(txn.data.payload)
            self._match_setups()
            self.out_toggle = Pid.DATA1
            # Hardware stops stalling at the SETUP. Whether software stalls this new request
            # is known from its intents, which may arrive before this observation.
            for direction in Direction:
                self.stall_active.discard((txn.endpoint, direction))

    def _check_address(self, txn: ObservedTransaction) -> None:
        if txn.address not in self.addresses:
            self.log.fail(SOURCE, f"device answered {txn} although its address is "
                                  f"{sorted(self.addresses)}")
        elif txn.address == self.latest_address:
            # The first answer at the new address ends the SET_ADDRESS transition: from now
            # on the old address must stay silent.
            self.addresses = {self.latest_address}

    def _check_stall(self, txn: ObservedTransaction, key: tuple[int, Direction]) -> None:
        stalled = txn.handshake is Pid.STALL and txn.handshake_source is Source.DEVICE
        if stalled:
            if key in self.stall_requested:
                self.stall_active.add(key)
            else:
                self.log.fail(SOURCE, f"{txn}: endpoint {key[0]} {key[1].name} answered STALL "
                                      "but software did not stall it")
        elif key in self.stall_active and txn.device_responded:
            self.log.fail(SOURCE, f"{txn}: endpoint {key[0]} {key[1].name} is halted and must answer STALL")

    def _check_in(self, txn: ObservedTransaction) -> None:
        queue = self.in_expected[txn.endpoint]
        payload = txn.data.payload
        if not self.log.check(bool(queue), SOURCE, f"{txn}: device sent IN data that software never armed"):
            return
        expected = queue[0]
        self.log.check(payload == expected, SOURCE,
                       f"{txn}: IN data on endpoint {txn.endpoint} is {payload.hex()}, software armed {expected.hex()}")
        if txn.complete:
            queue.popleft()
            self.matched += 1

    def check_phase(self):
        for data in self.setups_read:
            self.log.fail(SOURCE, f"software read SETUP {data.hex()}, which the device never ACKed")
        for data in self.out_read:
            self.log.fail(SOURCE, f"software read OUT data {data.hex() or '(zero length)'} on endpoint 0, "
                                  "which the device never ACKed")

    def report_phase(self):
        pending = {ep: len(q) for ep, q in self.in_expected.items() if q}
        self.logger.info("scoreboard: %d data transfers matched, armed but not sent: %s", self.matched, pending or "none")
