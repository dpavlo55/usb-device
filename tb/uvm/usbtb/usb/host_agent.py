"""USB full-speed host UVC: sequence items, driver, bus monitor and agent.

The driver is the host controller: it runs the 1 ms frame clock (SOF), executes one
transaction at a time (token, data, handshake, with the device's turnaround timeout) and
signals bus states (reset, suspend, resume). The monitor is independent of the driver: it
decodes every packet on the wire from either side and reports bus conditions.
"""
from __future__ import annotations

import enum
import random
from dataclasses import dataclass

import cocotb
from cocotb.handle import SimHandleBase
from cocotb.triggers import Event, First, Lock, ReadOnly, Timer
from cocotb.utils import get_sim_time
from pyuvm import (
    ConfigDB,
    uvm_active_passive_enum,
    uvm_agent,
    uvm_analysis_port,
    uvm_driver,
    uvm_monitor,
    uvm_sequence_item,
    uvm_sequencer,
)

from ..common.tlm import analysis_imp
from .line import LineDriver, LineEvent, LineObserver, ObservedPacket, PacketAssembler, Source, is_driving, line_state
from .timing import UsbTiming
from .wire import (
    LOW_SPEED_BIT_FS,
    DataPacket,
    HandshakePacket,
    LineState,
    Pid,
    SofPacket,
    TokenPacket,
    bits_to_fs,
    fs_to_bits,
)

TRANSACTION_BUDGET_BITS = 1500
"""Room a transaction needs before the next SOF (a 64-byte packet plus handshakes)."""
MAX_PACKET_BITS = 12_000


def now_fs() -> int:
    return round(get_sim_time(unit="fs"))


async def leave_read_only() -> None:
    """Step past the ReadOnly phase (1 fs) so the bus may be driven again. Needed after
    waking on the monitor, which publishes from ReadOnly."""
    await Timer(1, unit="fs")


@dataclass
class UsbHostConfig:
    dp: SimHandleBase
    dn: SimHandleBase
    dp_drive: SimHandleBase
    dn_drive: SimHandleBase
    timing: UsbTiming
    seed: int = 0
    handshake_delay_bits: tuple[float, float] = (2.0, 6.0)
    inter_packet_delay_bits: tuple[float, float] = (2.0, 4.0)
    """Bus idle the host leaves after a device packet before it transmits again (USB 2.0
    7.1.18.1: at least two bit times; the device may still be driving its EOP)."""
    is_active: uvm_active_passive_enum = uvm_active_passive_enum.UVM_ACTIVE


# Sequence items ------------------------------------------------------------------------------

class UsbTransactionItem(uvm_sequence_item):
    """One host transaction; the driver fills in what the device answered."""

    def __init__(self, name: str = "usb_transaction", kind: Pid = Pid.IN, address: int = 0,
                 endpoint: int = 0, payload: bytes = b"", data_pid: Pid | None = None,
                 isochronous: bool = False):
        super().__init__(name)
        self.kind = kind
        self.address = address
        self.endpoint = endpoint
        self.payload = bytes(payload)
        self.data_pid = data_pid or (Pid.DATA0 if kind is Pid.SETUP else None)
        self.isochronous = isochronous
        self.response: Pid | None = None
        self.response_data: bytes | None = None
        self.response_delay_bits: float | None = None
        self.corrupt = False

    @classmethod
    def setup(cls, address: int, payload: bytes, endpoint: int = 0) -> UsbTransactionItem:
        return cls("setup", Pid.SETUP, address, endpoint, payload, Pid.DATA0)

    @classmethod
    def out(cls, address: int, endpoint: int, payload: bytes, data_pid: Pid) -> UsbTransactionItem:
        return cls("out", Pid.OUT, address, endpoint, payload, data_pid)

    @classmethod
    def in_(cls, address: int, endpoint: int) -> UsbTransactionItem:
        return cls("in", Pid.IN, address, endpoint)

    @property
    def timed_out(self) -> bool:
        return self.response is None and not self.corrupt

    def __str__(self) -> str:
        sent = f" {self.data_pid.name}[{len(self.payload)}]" if self.kind is not Pid.IN else ""
        got = self.response.name if self.response else ("corrupt" if self.corrupt else "timeout")
        data = f"[{len(self.response_data)}]" if self.response_data is not None else ""
        return f"{self.kind.name} {self.address}.{self.endpoint}{sent} -> {got}{data}"


class BusOp(enum.Enum):
    RESET = "reset"
    SUSPEND = "suspend"
    RESUME = "resume"
    IDLE = "idle"
    WAIT_ATTACH = "wait for attach"
    WAIT_REMOTE_WAKEUP = "wait for remote wakeup"


class UsbBusItem(uvm_sequence_item):
    """A bus-level action of the host; ``happened`` reports waits that may time out."""

    def __init__(self, name: str = "usb_bus", op: BusOp = BusOp.IDLE, duration_fs: int = 0):
        super().__init__(name)
        self.op = op
        self.duration_fs = duration_fs
        self.happened = False
        self.elapsed_fs = 0

    def __str__(self) -> str:
        return f"{self.op.value} ({self.duration_fs / 1e9:.1f} us)"


# Driver --------------------------------------------------------------------------------------

class UsbHostDriver(uvm_driver):
    def build_phase(self):
        self.cfg: UsbHostConfig = ConfigDB().get(self, "", "cfg")
        self.timing = self.cfg.timing
        self.line = LineDriver(self.cfg.dp_drive, self.cfg.dn_drive)
        self.device_packet_export = analysis_imp("device_packet_export", self, self._on_packet)
        self._rng = random.Random(f"{self.cfg.seed}:host")
        self._bus = Lock()
        self._packets: list[ObservedPacket] = []
        self._packet_seen = Event()
        self._sof_sent = Event()
        self._device_eop_fs = 0
        self.sof_enabled = False
        self.frame_number = 0
        self._next_sof_fs = 0

    def _on_packet(self, observed: ObservedPacket) -> None:
        if observed.source is Source.DEVICE:
            self._device_eop_fs = max(self._device_eop_fs, observed.end_fs)
            self._packets.append(observed)
            self._packet_seen.set()

    async def _after_device_packet(self) -> None:
        """Hold off until the inter-packet delay after the last device packet has passed."""
        earliest = self._device_eop_fs + bits_to_fs(self._rng.uniform(*self.cfg.inter_packet_delay_bits))
        if now_fs() < earliest:
            await Timer(earliest - now_fs(), unit="fs")

    async def run_phase(self):
        self.line.release()
        cocotb.start_soon(self._frame_clock())
        while True:
            item = await self.seq_item_port.get_next_item()
            match item:
                case UsbTransactionItem():
                    await self._transaction(item)
                case UsbBusItem():
                    await self._bus_operation(item)
            self.logger.debug("%s", item)
            self.seq_item_port.item_done()

    # Frames --------------------------------------------------------------------------------

    def _start_frames(self) -> None:
        self.sof_enabled = True
        self._next_sof_fs = now_fs() + bits_to_fs(20)

    async def _frame_clock(self) -> None:
        while True:
            wait = self._next_sof_fs - now_fs() if self.sof_enabled else self.timing.frame_period_fs
            await Timer(max(wait, 1), unit="fs")
            if not self.sof_enabled or now_fs() < self._next_sof_fs:
                continue
            async with self._bus:
                if self.sof_enabled:
                    await self._after_device_packet()
                    await self.line.send_packet(SofPacket(self.frame_number))
                    self.frame_number = (self.frame_number + 1) & 0x7FF
                    self._next_sof_fs += self.timing.frame_period_fs
            self._sof_sent.set()
            self._sof_sent.clear()

    async def _room_in_frame(self) -> None:
        if self.sof_enabled and self._next_sof_fs - now_fs() < bits_to_fs(TRANSACTION_BUDGET_BITS):
            await self._sof_sent.wait()

    # Transactions ----------------------------------------------------------------------------

    async def _transaction(self, item: UsbTransactionItem) -> None:
        await self._room_in_frame()
        async with self._bus:
            await self._after_device_packet()
            self._packets.clear()
            await self.line.send_packet(TokenPacket(item.kind, item.address, item.endpoint))
            if item.kind is Pid.IN:
                await self._in_response(item)
                return
            await Timer(bits_to_fs(self._rng.uniform(2, 4)), unit="fs")
            await self.line.send_packet(DataPacket(item.data_pid, item.payload))
            if item.isochronous:
                return
            observed = await self._device_response()
            self._record(item, observed)

    async def _in_response(self, item: UsbTransactionItem) -> None:
        observed = await self._device_response()
        self._record(item, observed)
        if observed is None or not observed.ok or not isinstance(observed.packet, DataPacket):
            return
        item.response_data = observed.packet.payload
        if not item.isochronous:
            await Timer(bits_to_fs(self._rng.uniform(*self.cfg.handshake_delay_bits)), unit="fs")
            await self.line.send_packet(HandshakePacket(Pid.ACK))

    def _record(self, item: UsbTransactionItem, observed: ObservedPacket | None) -> None:
        if observed is None:
            return
        item.response_delay_bits = fs_to_bits(observed.start_fs - self._host_eop_fs)
        if not observed.ok:
            item.corrupt = True
            return
        item.response = observed.pid

    async def _device_response(self) -> ObservedPacket | None:
        """Wait for the device's packet: its SOP must come within the response timeout."""
        self._host_eop_fs = now_fs() - bits_to_fs(1)
        if not self._packets and not await self._device_starts(bits_to_fs(self.timing.response_timeout_bits)):
            return None
        deadline = now_fs() + bits_to_fs(MAX_PACKET_BITS)
        while not self._packets and now_fs() < deadline:
            timer = Timer(deadline - now_fs(), unit="fs")
            await First(self._packet_seen.wait(), timer)
            self._packet_seen.clear()
        await leave_read_only()
        return self._packets.pop(0) if self._packets else None

    async def _device_starts(self, window_fs: int) -> bool:
        deadline = now_fs() + window_fs
        while now_fs() < deadline:
            timer = Timer(deadline - now_fs(), unit="fs")
            if await First(self.cfg.dp.value_change, self.cfg.dn.value_change, timer) is timer:
                return False
            await ReadOnly()
            started = line_state(self.cfg.dp, self.cfg.dn) is not LineState.J
            await leave_read_only()
            if started:
                return True
        return False

    # Bus states ------------------------------------------------------------------------------

    async def _bus_operation(self, item: UsbBusItem) -> None:
        start = now_fs()
        match item.op:
            case BusOp.RESET:
                self.sof_enabled = False
                async with self._bus:
                    await self.line.hold(LineState.SE0, item.duration_fs)
                    self.line.release()
                self._start_frames()
            case BusOp.SUSPEND:
                self.sof_enabled = False
                await Timer(item.duration_fs, unit="fs")
            case BusOp.RESUME:
                await self._resume(item.duration_fs)
            case BusOp.IDLE:
                await Timer(item.duration_fs, unit="fs")
            case BusOp.WAIT_ATTACH:
                self.sof_enabled = False
                item.happened = await self._wait_for(lambda s: s is LineState.J, item.duration_fs)
            case BusOp.WAIT_REMOTE_WAKEUP:
                item.happened = await self._wait_for(
                    lambda s: s is LineState.K and not is_driving(self.cfg.dp_drive), item.duration_fs)
                item.elapsed_fs = now_fs() - start
                if item.happened:
                    # The upstream port answers a remote wakeup with its own resume signaling.
                    await self._resume(self.timing.remote_wakeup_resume_fs)
                return
        item.elapsed_fs = now_fs() - start

    async def _resume(self, duration_fs: int) -> None:
        async with self._bus:
            await self.line.hold(LineState.K, duration_fs)
            await self.line.hold(LineState.SE0, 2 * LOW_SPEED_BIT_FS)
            await self.line.hold(LineState.J, bits_to_fs(1))
            self.line.release()
        self._start_frames()

    async def _wait_for(self, condition, timeout_fs: int) -> bool:
        deadline = now_fs() + timeout_fs
        while True:
            await ReadOnly()
            met = condition(line_state(self.cfg.dp, self.cfg.dn))
            await leave_read_only()
            if met:
                return True
            remaining = deadline - now_fs()
            if remaining <= 0:
                return False
            timer = Timer(remaining, unit="fs")
            await First(self.cfg.dp.value_change, self.cfg.dn.value_change, timer)


# Monitor -------------------------------------------------------------------------------------

class UsbBusMonitor(uvm_monitor):
    """Publishes every packet on ``ap`` and every bus condition on ``line_event_ap``."""

    def build_phase(self):
        self.cfg: UsbHostConfig = ConfigDB().get(self, "", "cfg")
        self.ap = uvm_analysis_port("ap", self)
        self.line_event_ap = uvm_analysis_port("line_event_ap", self)
        self.assembler = PacketAssembler()
        self.packets: list[ObservedPacket] = []

    def _source(self) -> Source:
        return Source.HOST if is_driving(self.cfg.dp_drive) else Source.DEVICE

    async def run_phase(self):
        observer = LineObserver(self.cfg.dp, self.cfg.dn, self._source)
        async for run in observer.runs():
            for observation in self.assembler.feed(run):
                if isinstance(observation, LineEvent):
                    self.logger.info("bus: %s", observation)
                    self.line_event_ap.write(observation)
                else:
                    self.logger.debug("bus: %s", observation)
                    self.ap.write(observation)


class UsbHostAgent(uvm_agent):
    def build_phase(self):
        self.cfg: UsbHostConfig = ConfigDB().get(self, "", "cfg")
        ConfigDB().set(self, "*", "cfg", self.cfg)
        self.monitor = UsbBusMonitor("monitor", self)
        if self.cfg.is_active == uvm_active_passive_enum.UVM_ACTIVE:
            self.sequencer = uvm_sequencer("sequencer", self)
            self.driver = UsbHostDriver("driver", self)

    def connect_phase(self):
        if self.cfg.is_active == uvm_active_passive_enum.UVM_ACTIVE:
            self.driver.seq_item_port.connect(self.sequencer.seq_item_export)
            self.monitor.ap.connect(self.driver.device_packet_export)

