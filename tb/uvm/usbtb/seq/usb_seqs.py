"""Sequences on the USB host sequencer: single transactions, bus actions, control transfers.

:class:`ControlTransferSeq` is the host side of USB 2.0 section 8.5.3 and 9.2.6: SETUP, the
data stage packet by packet (stopping at a short packet or at wLength), the status stage
in the opposite direction, NAK retries within the request time limits, and up to three
attempts for a transaction that gets no valid answer.
"""
from __future__ import annotations

from dataclasses import dataclass

from cocotb.triggers import Timer
from cocotb.utils import get_sim_time
from pyuvm import uvm_sequence

from ..ch9.constants import Direction
from ..ch9.setup import SetupPacket
from ..usb.host_agent import BusOp, UsbBusItem, UsbTransactionItem
from ..usb.timing import UsbTiming
from ..usb.wire import DATA_PIDS, Pid, bits_to_fs, other_data_pid

ERROR_RETRIES = 3
NAK_RETRY_GAP_BITS = 40


class UsbTransactionSeq(uvm_sequence):
    """Runs one :class:`UsbTransactionItem`; the item carries the result back."""

    def __init__(self, item: UsbTransactionItem, name: str = "usb_transaction_seq"):
        super().__init__(name)
        self.item = item

    async def body(self):
        await self.start_item(self.item)
        await self.finish_item(self.item)


class UsbBusSeq(uvm_sequence):
    def __init__(self, op: BusOp, duration_fs: int = 0, name: str = "usb_bus_seq"):
        super().__init__(name)
        self.item = UsbBusItem(op.value, op, duration_fs)

    async def body(self):
        await self.start_item(self.item)
        await self.finish_item(self.item)


@dataclass
class ControlResult:
    setup: SetupPacket
    data: bytes = b""
    stalled: bool = False
    stage: str = ""
    error: str | None = None
    naks: int = 0

    @property
    def ok(self) -> bool:
        return not self.stalled and self.error is None

    def __str__(self) -> str:
        if self.stalled:
            return f"{self.setup}: STALL in {self.stage} stage"
        if self.error:
            return f"{self.setup}: {self.error} in {self.stage} stage"
        return f"{self.setup}: OK" + (f", {len(self.data)} bytes" if self.data else "")


class _StageFailed(Exception):
    pass


class ControlTransferSeq(uvm_sequence):
    def __init__(self, address: int, setup: SetupPacket, timing: UsbTiming, max_packet_size0: int = 64,
                 out_data: bytes = b"", name: str = "control_transfer_seq"):
        super().__init__(name)
        self.address = address
        self.setup = setup
        self.timing = timing
        self.max_packet_size0 = max_packet_size0
        self.out_data = out_data
        self.result = ControlResult(setup)

    async def body(self):
        try:
            await self._stages()
        except _StageFailed:
            pass

    async def _stages(self) -> None:
        setup, result = self.setup, self.result
        result.stage = "setup"
        txn = await self._attempt(lambda: UsbTransactionItem.setup(self.address, setup.to_bytes()))
        self._expect(txn, {Pid.ACK})
        if setup.w_length and setup.direction is Direction.IN:
            result.stage = "data"
            await self._data_in()
            result.stage = "status"
            txn = await self._until_answered(lambda: UsbTransactionItem.out(self.address, 0, b"", Pid.DATA1))
            self._expect(txn, {Pid.ACK})
        else:
            if setup.w_length:
                result.stage = "data"
                await self._data_out()
            result.stage = "status"
            txn = await self._until_answered(lambda: UsbTransactionItem.in_(self.address, 0))
            self._expect(txn, {Pid.DATA1})
            if txn.response_data:
                self._fail(f"status stage returned {len(txn.response_data)} data bytes")
        result.stage = ""

    async def _data_in(self) -> None:
        toggle = Pid.DATA1
        while True:
            txn = await self._until_answered(lambda: UsbTransactionItem.in_(self.address, 0))
            self._expect(txn, set(DATA_PIDS))
            if txn.response is not toggle:
                self._fail(f"data toggle {txn.response.name}, expected {toggle.name}")
            self.result.data += txn.response_data
            toggle = other_data_pid(toggle)
            short = len(txn.response_data) < self.max_packet_size0
            if short or len(self.result.data) >= self.setup.w_length:
                if len(self.result.data) > self.setup.w_length:
                    self._fail(f"{len(self.result.data)} bytes returned for wLength {self.setup.w_length}")
                return

    async def _data_out(self) -> None:
        toggle = Pid.DATA1
        data = self.out_data[: self.setup.w_length]
        for offset in range(0, max(len(data), 1), self.max_packet_size0):
            chunk = data[offset:offset + self.max_packet_size0]
            txn = await self._until_answered(lambda: UsbTransactionItem.out(self.address, 0, chunk, toggle))
            self._expect(txn, {Pid.ACK})
            toggle = other_data_pid(toggle)

    def _expect(self, txn: UsbTransactionItem, allowed: set[Pid]) -> None:
        if txn.response is Pid.STALL:
            self.result.stalled = True
            raise _StageFailed
        if txn.response not in allowed:
            got = txn.response.name if txn.response else ("corrupt answer" if txn.corrupt else "no answer")
            self._fail(f"{txn.kind.name} got {got}")

    def _fail(self, message: str) -> None:
        self.result.error = message
        raise _StageFailed

    async def _attempt(self, make) -> UsbTransactionItem:
        """Up to three tries for a transaction that gets no valid answer (section 8.5.3.3)."""
        for _ in range(ERROR_RETRIES):
            txn = make()
            await UsbTransactionSeq(txn).start(self.sequencer)
            if not txn.timed_out and not txn.corrupt:
                return txn
        return txn

    async def _until_answered(self, make) -> UsbTransactionItem:
        """Repeat a NAKed transaction until it is answered or the stage time limit expires."""
        deadline = get_sim_time(unit="fs") + self.timing.request_timeout_fs
        while True:
            txn = await self._attempt(make)
            if txn.response is not Pid.NAK:
                return txn
            self.result.naks += 1
            if get_sim_time(unit="fs") > deadline:
                self._fail(f"still NAKed after {self.timing.request_timeout_fs / 1e12:.1f} ms")
            await Timer(bits_to_fs(NAK_RETRY_GAP_BITS), unit="fs")
