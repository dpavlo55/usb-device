"""Packets, transactions and control transfers are rebuilt correctly from bus activity."""
from itertools import groupby

from usbtb.ch9.setup import SetupPacket
from usbtb.usb.line import LineCondition, LineEvent, LineRun, ObservedPacket, PacketAssembler, Source
from usbtb.usb.transfers import ControlOutcome, ControlTransferAssembler, TransactionAssembler
from usbtb.usb.wire import (
    BIT_TIME_FS,
    MS_FS,
    DataPacket,
    HandshakePacket,
    LineState,
    Pid,
    TokenPacket,
    encode_packet,
)


class Bus:
    """Lays packets end to end on a timeline, as line runs, with idle J between them."""

    def __init__(self):
        self.time = 0
        self.runs: list[LineRun] = []

    def idle(self, bits: float) -> None:
        self._run(LineState.J, bits, Source.HOST)

    def packet(self, packet, source=Source.HOST) -> None:
        states = [cell.state for cell in encode_packet(packet)][:-1]  # the final J is idle
        for state, group in groupby(states):
            self._run(state, len(list(group)), source)
        self.idle(3)

    def _run(self, state: LineState, bits: float, source: Source) -> None:
        duration = round(bits * BIT_TIME_FS)
        if self.runs and self.runs[-1].state is state:
            last = self.runs.pop()
            self.runs.append(LineRun(state, last.start_fs, last.duration_fs + duration, last.source))
        else:
            self.runs.append(LineRun(state, self.time, duration, source))
        self.time += duration

    def observe(self) -> list:
        assembler = PacketAssembler()
        return [o for run in self.runs for o in assembler.feed(run)]


def test_packets_round_trip_through_line_runs():
    bus = Bus()
    sent = [TokenPacket(Pid.SETUP, 5, 0), DataPacket(Pid.DATA0, bytes(range(8))), HandshakePacket(Pid.ACK),
            DataPacket(Pid.DATA1, b"\xff" * 20)]
    bus.idle(10)
    for packet in sent:
        bus.packet(packet)
    observed = bus.observe()
    assert [o.packet for o in observed] == sent
    assert all(o.ok for o in observed)


def test_bus_conditions():
    bus = Bus()
    bus.idle(10)
    bus._run(LineState.SE0, 10 * MS_FS / BIT_TIME_FS, Source.HOST)
    bus.idle(4 * MS_FS / BIT_TIME_FS)
    bus._run(LineState.K, 2 * MS_FS / BIT_TIME_FS, Source.DEVICE)
    bus.idle(5)
    conditions = [(o.condition, o.source) for o in bus.observe() if isinstance(o, LineEvent)]
    assert conditions == [(LineCondition.RESET, Source.HOST), (LineCondition.IDLE, Source.HOST),
                          (LineCondition.RESUME, Source.DEVICE)]


def _observed(packet, source, start):
    return ObservedPacket(packet, source, start, start + 100)


def test_transactions_and_control_transfer():
    setup = SetupPacket.get_descriptor(1, length=18)
    stream = [
        (TokenPacket(Pid.SETUP, 0, 0), Source.HOST), (DataPacket(Pid.DATA0, setup.to_bytes()), Source.HOST),
        (HandshakePacket(Pid.ACK), Source.DEVICE),
        (TokenPacket(Pid.IN, 0, 0), Source.HOST), (HandshakePacket(Pid.NAK), Source.DEVICE),
        (TokenPacket(Pid.IN, 0, 0), Source.HOST), (DataPacket(Pid.DATA1, b"A" * 8), Source.DEVICE),
        (HandshakePacket(Pid.ACK), Source.HOST),
        (TokenPacket(Pid.IN, 0, 0), Source.HOST), (DataPacket(Pid.DATA0, b"B" * 8), Source.DEVICE),
        (HandshakePacket(Pid.ACK), Source.HOST),
        (TokenPacket(Pid.IN, 0, 0), Source.HOST), (DataPacket(Pid.DATA1, b"C" * 2), Source.DEVICE),
        (HandshakePacket(Pid.ACK), Source.HOST),
        (TokenPacket(Pid.OUT, 0, 0), Source.HOST), (DataPacket(Pid.DATA1, b""), Source.HOST),
        (HandshakePacket(Pid.ACK), Source.DEVICE),
    ]
    transactions, controls = TransactionAssembler(), ControlTransferAssembler()
    observed_txns, transfers = [], []
    for time, (packet, source) in enumerate(stream):
        for txn in transactions.feed(_observed(packet, source, 1000 * time)):
            observed_txns.append(txn)
            transfers += controls.feed(txn)
    assert [t.kind for t in observed_txns] == [Pid.SETUP, Pid.IN, Pid.IN, Pid.IN, Pid.IN, Pid.OUT]
    assert all(t.device_responded for t in observed_txns)
    (transfer,) = transfers
    assert transfer.outcome is ControlOutcome.COMPLETED
    assert transfer.setup == setup and transfer.data == b"A" * 8 + b"B" * 8 + b"CC"
    assert transfer.naks == 1 and transfer.data_pids == [Pid.DATA1, Pid.DATA0, Pid.DATA1]


def test_stall_ends_a_control_transfer():
    setup = SetupPacket.get_descriptor(0x55, length=8)
    stream = [(TokenPacket(Pid.SETUP, 3, 0), Source.HOST), (DataPacket(Pid.DATA0, setup.to_bytes()), Source.HOST),
              (HandshakePacket(Pid.ACK), Source.DEVICE), (TokenPacket(Pid.IN, 3, 0), Source.HOST),
              (HandshakePacket(Pid.STALL), Source.DEVICE), (TokenPacket(Pid.SETUP, 3, 0), Source.HOST)]
    transactions, controls, transfers = TransactionAssembler(), ControlTransferAssembler(), []
    for time, (packet, source) in enumerate(stream):
        for txn in transactions.feed(_observed(packet, source, 1000 * time)):
            transfers += controls.feed(txn)
    assert [(t.outcome, t.stalled_in) for t in transfers] == [(ControlOutcome.STALLED, "data")]
