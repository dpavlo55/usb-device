"""The device software: what firmware on the CPU would do with the USB core.

It reacts to hardware events, runs control transfers on endpoint 0 (SETUP, data stage,
status stage) with :class:`~usbtb.ch9.device.Ch9Device` deciding the answers, and programs
the endpoint entries for each configuration and alternate setting. It talks to hardware
only through a :class:`~usbtb.device.hal.DeviceHal`, so the same software drives the RTL
and the reference model.
"""
from __future__ import annotations

import logging
import random
from collections.abc import Callable

import cocotb
from cocotb.queue import Queue
from cocotb.task import Task
from cocotb.triggers import Timer
from cocotb.utils import get_sim_time

from ..ch9.constants import Direction, TransferType
from ..ch9.descriptors import Configuration, DescriptorSet, EndpointDescriptor
from ..ch9.device import (
    Action,
    Ch9Device,
    Configure,
    HaltEndpoint,
    SelectAlternate,
    SetAddress,
    SoftwareFault,
)
from ..ch9.profile import HardwareLimits
from ..ch9.setup import SetupPacket
from .hal import (
    BusReset,
    DeviceHal,
    EP0_BUFFER_BYTES,
    EP0_IN,
    EP0_OUT,
    ENTRIES,
    EntryConfig,
    Resumed,
    SetupReceived,
    Suspended,
    TransferDone,
)
from .intent import AddressChanged, InDataArmed, Intent, OutDataReceived, SetupRead, StallChanged

REMOTE_WAKEUP_MIN_SUSPEND_FS = 5_000_000_000_000
"""TWTRSM: the bus must have been suspended for 5 ms before a device may signal wakeup."""


class _ControlAborted(Exception):
    """A new SETUP or a bus reset ended the control transfer in progress."""


def control_packets(data: bytes, max_packet_size: int, w_length: int) -> list[bytes]:
    """Split an IN data stage into packets, ending with a zero-length packet when the host
    could otherwise not tell that the data ended (USB 2.0 section 5.5.3)."""
    packets = [data[i:i + max_packet_size] for i in range(0, len(data), max_packet_size)]
    if not packets or (len(data) < w_length and len(data) % max_packet_size == 0):
        packets.append(b"")
    return packets


class DeviceSoftware:
    def __init__(self, hal: DeviceHal, descriptors: DescriptorSet, *,
                 publish: Callable[[Intent], None], logger: logging.Logger,
                 rng: random.Random, max_latency_ns: int = 0,
                 faults: frozenset[SoftwareFault] = frozenset()):
        self.hal = hal
        self.descriptors = descriptors
        self.ch9 = Ch9Device(descriptors, faults)
        self.publish = publish
        self.logger = logger
        self.rng = rng
        self.max_latency_ns = max_latency_ns
        self.max_packet_size0 = descriptors.device.max_packet_size0
        self.suspended_at_fs: int | None = None
        self.bus_events: list[str] = []
        """"suspend" and "resume" in the order the HAL reported them."""
        self.entries: dict[int, int] = {}
        """USB endpoint address (0x81, 0x02, ...) to entry index, for the active configuration."""
        self._ep0_done: Queue[TransferDone | None] = Queue()
        """Completions of both endpoint 0 entries; ``None`` aborts the control transfer."""
        self._control: Task | None = None

    # Main loop -------------------------------------------------------------------------------

    async def run(self) -> None:
        await self.hal.start()
        await self._configure_ep0()
        await self.hal.set_pull_up(True)
        self.logger.info("device software running, pull-up enabled")
        while True:
            event = await self.hal.next_event()
            # Firmware reaction time (randomized; a slow reaction shows up as NAKs).
            await Timer(self.rng.randint(1, max(1, self.max_latency_ns)), unit="ns")
            match event:
                case SetupReceived(data=data):
                    await self._abort_control()
                    self._control = cocotb.start_soon(self._control_transfer(data))
                case TransferDone(entry=entry) if entry in (EP0_OUT, EP0_IN):
                    self._ep0_done.put_nowait(event)
                case TransferDone():
                    pass  # no application uses the other endpoints
                case BusReset():
                    await self._bus_reset()
                case Suspended():
                    self.suspended_at_fs = round(get_sim_time(unit="fs"))
                    self.bus_events.append("suspend")
                case Resumed():
                    self.suspended_at_fs = None
                    self.bus_events.append("resume")

    def stop(self) -> None:
        """Hard stop on reset: the whole environment restarts, so cancelling is safe."""
        if self._control is not None:
            self._control.cancel()

    async def request_remote_wakeup(self) -> bool:
        """The user's wakeup action (USBCV: 'generate a remote wakeup event on the device').

        Signals only while suspended and while the host has enabled remote wakeup."""
        if self.suspended_at_fs is None or not self.ch9.remote_wakeup_allowed:
            self.logger.info("remote wakeup requested: not signaled (suspended=%s, enabled=%s)",
                             self.suspended_at_fs is not None, self.ch9.remote_wakeup_enabled)
            return False
        waited = round(get_sim_time(unit="fs")) - self.suspended_at_fs
        if waited < REMOTE_WAKEUP_MIN_SUSPEND_FS:
            await Timer(REMOTE_WAKEUP_MIN_SUSPEND_FS - waited, unit="fs")
        self.logger.info("signaling remote wakeup")
        await self.hal.signal_remote_wakeup()
        return True

    # Control transfers -----------------------------------------------------------------------

    async def _control_transfer(self, raw: bytes) -> None:
        try:
            await self._control_stages(SetupPacket.from_bytes(raw), raw)
        except _ControlAborted:
            self.logger.info("control transfer aborted")

    async def _control_stages(self, setup: SetupPacket, raw: bytes) -> None:
        self.publish(SetupRead(raw))
        response = self.ch9.handle(setup)
        self.logger.info("SETUP %s -> %s", setup, "STALL" if response.stall else
                         f"{len(response.data)} bytes" if response.data is not None else "OK")
        if response.stall or (setup.direction is Direction.OUT and setup.w_length):
            # Standard requests have no OUT data stage the device accepts (SET_DESCRIPTOR
            # is optional and unsupported): a request error stalls the data stage.
            await self._stall_ep0()
            return
        if setup.direction is Direction.IN and setup.w_length:
            await self._data_in_stage(response.data, setup.w_length)
            return
        for action in response.actions:
            if not isinstance(action, SetAddress):
                await self._apply(action)
        await self._arm_in(b"")
        while (await self._wait_ep0()).entry != EP0_IN:
            pass
        for action in response.actions:
            if isinstance(action, SetAddress):
                await self._apply(action)

    async def _data_in_stage(self, data: bytes, w_length: int) -> None:
        """Send the data packet by packet. The status OUT is armed first: the host may end
        the data stage early (a short read such as GET_DESCRIPTOR(DEVICE, 64) with a
        smaller bMaxPacketSize0) and go straight to the status stage."""
        await self.hal.arm_out(EP0_OUT)
        for packet in control_packets(data, self.max_packet_size0, w_length):
            await self._arm_in(packet)
            done = await self._wait_ep0()
            if done.entry == EP0_OUT:
                self.publish(OutDataReceived(0, done.data))
                return
        while (done := await self._wait_ep0()).entry != EP0_OUT:
            pass
        self.publish(OutDataReceived(0, done.data))

    async def _arm_in(self, data: bytes) -> None:
        await self.hal.arm_in(EP0_IN, data)
        self.publish(InDataArmed(0, data))

    async def _wait_ep0(self) -> TransferDone:
        done = await self._ep0_done.get()
        if done is None:
            raise _ControlAborted
        return done

    async def _stall_ep0(self) -> None:
        for entry, direction in ((EP0_OUT, Direction.OUT), (EP0_IN, Direction.IN)):
            await self.hal.set_stall(entry, True)
            self.publish(StallChanged(0, direction, True))

    async def _abort_control(self) -> None:
        """End the control transfer in progress at its next wait for hardware.

        The task is not cancelled: a register access cut in half could leave a stale bus
        item behind. Hardware has already cleared ``armed`` on the new SETUP.
        """
        task, self._control = self._control, None
        if task is not None and not task.done():
            self._ep0_done.put_nowait(None)
            await task
        while not self._ep0_done.empty():
            self._ep0_done.get_nowait()

    # Hardware actions ------------------------------------------------------------------------

    async def _apply(self, action: Action) -> None:
        match action:
            case SetAddress(address=address):
                await self.hal.set_address(address)
                self.publish(AddressChanged(address))
            case Configure(configuration=configuration):
                await self._configure(configuration)
            case SelectAlternate(configuration=configuration, interface=interface):
                await self._select_alternate(configuration, interface)
            case HaltEndpoint(endpoint=endpoint, halted=halted):
                entry = self.entries[endpoint.endpoint_address]
                await self.hal.set_stall(entry, halted)
                if not halted:
                    await self.hal.reset_toggle(entry)
                self.publish(StallChanged(endpoint.number, endpoint.direction, halted))
        self.ch9.complete(action)

    async def _configure_ep0(self) -> None:
        await self.hal.configure_entry(EP0_OUT, EntryConfig(0, Direction.OUT, TransferType.CONTROL,
                                                            0, EP0_BUFFER_BYTES))
        await self.hal.configure_entry(EP0_IN, EntryConfig(0, Direction.IN, TransferType.CONTROL,
                                                           EP0_BUFFER_BYTES, EP0_BUFFER_BYTES))

    async def _configure(self, configuration: Configuration | None) -> None:
        """Allocate one entry and buffer per endpoint; enable those of alternate setting 0."""
        for entry in range(2, ENTRIES):
            await self.hal.disable_entry(entry)
        self._publish_unstalled(self.entries)
        self.entries = {}
        if configuration is None:
            return
        base = 2 * EP0_BUFFER_BYTES
        for entry, (interface, endpoint) in enumerate(_endpoints(configuration), start=2):
            size = HardwareLimits.buffer_size(endpoint.max_packet_size)
            enabled = interface.descriptor.alternate_setting == 0
            await self.hal.configure_entry(entry, EntryConfig(endpoint.number, endpoint.direction,
                                                              endpoint.transfer_type, base, size, enabled))
            self.entries[endpoint.endpoint_address] = entry
            base += size

    async def _select_alternate(self, configuration: Configuration, interface: int) -> None:
        alternate = self.ch9.alternates[interface]
        for alt in configuration.alternates(interface):
            for endpoint in alt.endpoints:
                entry = self.entries[endpoint.endpoint_address]
                size = HardwareLimits.buffer_size(endpoint.max_packet_size)
                enabled = alt.descriptor.alternate_setting == alternate
                base = self._base_of(configuration, endpoint)
                await self.hal.configure_entry(entry, EntryConfig(endpoint.number, endpoint.direction,
                                                                  endpoint.transfer_type, base, size, enabled))
                self.publish(StallChanged(endpoint.number, endpoint.direction, False))

    def _base_of(self, configuration: Configuration, target: EndpointDescriptor) -> int:
        base = 2 * EP0_BUFFER_BYTES
        for _, endpoint in _endpoints(configuration):
            if endpoint is target:
                return base
            base += HardwareLimits.buffer_size(endpoint.max_packet_size)
        raise KeyError(target)

    def _publish_unstalled(self, entries: dict[int, int]) -> None:
        for address in entries:
            self.publish(StallChanged(address & 0x0F, Direction(address >> 7), False))

    async def _bus_reset(self) -> None:
        """Back to the Default state (the RDL's ``bus_event.bus_reset`` contract)."""
        await self._abort_control()
        self.ch9.bus_reset()
        self.suspended_at_fs = None
        self.publish(AddressChanged(0))
        await self._configure(None)
        await self._configure_ep0()
        for direction in Direction:
            self.publish(StallChanged(0, direction, False))


def _endpoints(configuration: Configuration):
    return [(interface, endpoint) for interface in configuration.interfaces for endpoint in interface.endpoints]
