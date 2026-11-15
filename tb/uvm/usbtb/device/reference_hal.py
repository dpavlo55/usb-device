"""A behavioral model of the device core, used to check the testbench itself.

It implements the hardware side of the contract in ``rdl/usb_device.rdl`` directly on the
bus pins: token address filtering, endpoint entries with arm/complete ownership, data
toggles, SETUP capture, NAK/STALL, bus reset, suspend/resume detection and remote wakeup.
With the device software on top, every Chapter 9 test can run (and must pass) before the
RTL exists. :class:`HardwareFault` switches on deliberate bugs so the self-check tests can
prove that the scoreboard and protocol checker catch them.
"""
from __future__ import annotations

import enum
import logging
import random
from dataclasses import dataclass

import cocotb
from cocotb.handle import SimHandleBase
from cocotb.queue import Queue
from cocotb.triggers import Timer
from cocotb.utils import get_sim_time

from ..ch9.constants import Direction, TransferType
from ..usb.line import LineCondition, LineDriver, LineEvent, LineObserver, ObservedPacket, PacketAssembler, Source
from ..usb.wire import (
    MS_FS,
    DataPacket,
    HandshakePacket,
    LineState,
    Packet,
    Pid,
    SofPacket,
    TokenPacket,
    bits_to_fs,
    other_data_pid,
)
from .hal import (
    BusReset,
    DeviceHal,
    EntryConfig,
    HalEvent,
    Resumed,
    SetupReceived,
    Suspended,
    TransferDone,
)

SUSPEND_IDLE_FS = 3 * MS_FS
REMOTE_WAKEUP_K_FS = 2 * MS_FS


class HardwareFault(enum.Enum):
    WRONG_DATA_TOGGLE = "sends the wrong DATA PID on endpoint 0 IN"
    CORRUPT_IN_DATA = "flips the first byte of IN data"
    SLOW_RESPONSE = "answers 10 bit times after the host packet"
    RESPOND_TO_ANY_ADDRESS = "ignores the device address"


@dataclass
class _Entry:
    config: EntryConfig
    armed: bool = False
    data: bytes = b""
    stall: bool = False
    toggle: Pid = Pid.DATA0


@dataclass(frozen=True)
class ReferencePins:
    dp: SimHandleBase
    dn: SimHandleBase
    dp_drive: SimHandleBase
    dn_drive: SimHandleBase
    pull_up: SimHandleBase


class ReferenceHal(DeviceHal):
    def __init__(self, pins: ReferencePins, logger: logging.Logger, rng: random.Random,
                 faults: frozenset[HardwareFault] = frozenset()):
        self.pins = pins
        self.line = LineDriver(pins.dp_drive, pins.dn_drive)
        self.logger = logger
        self.rng = rng
        self.faults = faults
        self.address = 0
        self.entries: dict[int, _Entry] = {}
        self.suspended = False
        self._events: Queue[HalEvent] = Queue()
        self._driving = False
        self._token: TokenPacket | None = None
        self._awaiting_ack: int | None = None
        self._last_activity_fs = 0
        self._tasks: list = []

    # DeviceHal -------------------------------------------------------------------------------

    async def start(self) -> None:
        self.line.release()
        self.pins.pull_up.value = 0
        self._tasks = [cocotb.start_soon(self._engine()), cocotb.start_soon(self._suspend_watchdog())]

    def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        self.line.release()
        self.pins.pull_up.value = 0

    async def next_event(self) -> HalEvent:
        return await self._events.get()

    async def configure_entry(self, entry: int, config: EntryConfig) -> None:
        self.entries[entry] = _Entry(config)

    async def disable_entry(self, entry: int) -> None:
        if entry in self.entries:
            self.entries[entry].config = _disabled(self.entries[entry].config)
            self.entries[entry].stall = False

    async def arm_in(self, entry: int, data: bytes) -> None:
        self.entries[entry].data = bytes(data)
        self.entries[entry].armed = True

    async def arm_out(self, entry: int) -> None:
        self.entries[entry].armed = True

    async def set_stall(self, entry: int, stalled: bool) -> None:
        self.entries[entry].stall = stalled

    async def reset_toggle(self, entry: int) -> None:
        self.entries[entry].toggle = Pid.DATA0

    async def set_address(self, address: int) -> None:
        self.address = address

    async def set_pull_up(self, enabled: bool) -> None:
        self.pins.pull_up.value = int(enabled)

    async def signal_remote_wakeup(self) -> None:
        if self.suspended:
            cocotb.start_soon(self._drive_resume())

    # Bus engine ------------------------------------------------------------------------------

    async def _engine(self) -> None:
        assembler = PacketAssembler()
        observer = LineObserver(self.pins.dp, self.pins.dn,
                                lambda: Source.DEVICE if self._driving else Source.HOST)
        async for run in observer.runs():
            self._last_activity_fs = run.start_fs + run.duration_fs
            if self.suspended:
                self.suspended = False
                self._events.put_nowait(Resumed())
            for observation in assembler.feed(run):
                match observation:
                    case LineEvent(condition=LineCondition.RESET, source=Source.HOST):
                        self._token, self._awaiting_ack = None, None
                        self.address = 0
                        self._events.put_nowait(BusReset())
                    case ObservedPacket(source=Source.HOST) if observation.ok:
                        self._on_host_packet(observation.packet)
                    case ObservedPacket(source=Source.HOST):
                        self._token = None

    async def _suspend_watchdog(self) -> None:
        while True:
            idle = round(get_sim_time(unit="fs")) - self._last_activity_fs
            if idle < SUSPEND_IDLE_FS:
                await Timer(SUSPEND_IDLE_FS - idle, unit="fs")
                continue
            attached = int(self.pins.pull_up.value) == 1
            if attached and not self.suspended and not self._driving:
                self.suspended = True
                self._events.put_nowait(Suspended())
            await Timer(SUSPEND_IDLE_FS, unit="fs")

    def _on_host_packet(self, packet: Packet) -> None:
        match packet:
            case TokenPacket():
                self._awaiting_ack = None
                addressed = packet.address == self.address or HardwareFault.RESPOND_TO_ANY_ADDRESS in self.faults
                self._token = packet if addressed else None
                if self._token and packet.pid is Pid.IN:
                    self._token = None
                    self._on_in(packet.endpoint)
            case DataPacket() if self._token is not None:
                token, self._token = self._token, None
                if token.pid is Pid.SETUP:
                    self._on_setup(token.endpoint, packet)
                else:
                    self._on_out(token.endpoint, packet)
            case HandshakePacket(pid=Pid.ACK) if self._awaiting_ack is not None:
                entry = self.entries[self._awaiting_ack]
                self._awaiting_ack = None
                entry.armed = False
                entry.toggle = other_data_pid(entry.toggle)
                self._events.put_nowait(TransferDone(self._index(entry)))
            case SofPacket():
                pass
            case _:
                self._token = None

    def _find(self, number: int, direction: Direction) -> _Entry | None:
        for entry in self.entries.values():
            config = entry.config
            if config.enable and config.endpoint_number == number and config.direction is direction:
                return entry
        return None

    def _index(self, target: _Entry) -> int:
        return next(index for index, entry in self.entries.items() if entry is target)

    def _on_setup(self, number: int, packet: DataPacket) -> None:
        entry = self._find(number, Direction.OUT)
        if entry is None or entry.config.kind is not TransferType.CONTROL:
            return
        if packet.pid is not Pid.DATA0 or len(packet.payload) != 8:
            return
        self._respond(HandshakePacket(Pid.ACK))
        for other in self.entries.values():
            if other.config.endpoint_number == number and other.config.kind is TransferType.CONTROL:
                other.stall, other.armed, other.toggle = False, False, Pid.DATA1
        self._events.put_nowait(SetupReceived(packet.payload))

    def _on_out(self, number: int, packet: DataPacket) -> None:
        entry = self._find(number, Direction.OUT)
        if entry is None:
            return
        isochronous = entry.config.kind is TransferType.ISOCHRONOUS
        if entry.stall:
            self._respond(HandshakePacket(Pid.STALL))
        elif not entry.armed:
            if not isochronous:
                self._respond(HandshakePacket(Pid.NAK))
        elif packet.pid is not entry.toggle and not isochronous:
            self._respond(HandshakePacket(Pid.ACK))
        else:
            entry.armed = False
            entry.toggle = other_data_pid(entry.toggle)
            if not isochronous:
                self._respond(HandshakePacket(Pid.ACK))
            self._events.put_nowait(TransferDone(self._index(entry), packet.payload))

    def _on_in(self, number: int) -> None:
        entry = self._find(number, Direction.IN)
        if entry is None:
            return
        if entry.stall:
            self._respond(HandshakePacket(Pid.STALL))
        elif not entry.armed:
            self._respond(HandshakePacket(Pid.NAK))
        else:
            pid, data = entry.toggle, entry.data
            if HardwareFault.WRONG_DATA_TOGGLE in self.faults and number == 0:
                pid = other_data_pid(pid)
            if HardwareFault.CORRUPT_IN_DATA in self.faults and data:
                data = bytes((data[0] ^ 0xFF,)) + data[1:]
            self._awaiting_ack = self._index(entry)
            self._respond(DataPacket(pid, data))

    def _respond(self, packet: Packet) -> None:
        delay_bits = 10 if HardwareFault.SLOW_RESPONSE in self.faults else self.rng.uniform(2, 5)
        self._driving = True
        cocotb.start_soon(self._transmit(packet, delay_bits))

    async def _transmit(self, packet: Packet, delay_bits: float) -> None:
        await Timer(bits_to_fs(delay_bits), unit="fs")
        await self.line.send_packet(packet)
        self._driving = False

    async def _drive_resume(self) -> None:
        self._driving = True
        await self.line.hold(LineState.K, REMOTE_WAKEUP_K_FS)
        self.line.release()
        self._driving = False


def _disabled(config: EntryConfig) -> EntryConfig:
    return EntryConfig(config.endpoint_number, config.direction, config.kind, config.base_address,
                       config.buffer_bytes, enable=False)

