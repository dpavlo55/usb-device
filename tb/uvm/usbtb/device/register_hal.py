"""The device HAL on the real core: every operation is a RAL access over AXI4-Lite.

Field positions come from the generated register model, never from hand-written
constants. Registers that software owns are kept in a shadow here and written whole, so
no read-modify-write races with hardware (``stall`` is cleared by hardware on SETUP, for
example, so its mirror cannot be trusted for RMW). Events are found through the
``interrupt`` summary register: while it reads 0 the HAL sleeps until usb_top's ``irq``
rises, as interrupt-driven firmware would. Without an ``irq`` handle it polls.
"""
from __future__ import annotations

import logging

import cocotb
from cocotb.queue import Queue
from cocotb.triggers import RisingEdge, Timer
from pyuvm import uvm_reg, uvm_status_e

from ..ch9.constants import Direction, TransferType
from ..ral import usb_ram
from ..ral.usb_device_ral import usb_device
from .hal import (
    BusReset,
    DeviceHal,
    EP0_BUFFER_BYTES,
    ENTRIES,
    EntryConfig,
    RDL_ENDPOINT_KIND,
    HalEvent,
    Resumed,
    SetupReceived,
    Suspended,
    TransferDone,
)


SETUP_BYTES = 8


class RegisterAccessError(RuntimeError):
    pass


class RegisterHal(DeviceHal):
    def __init__(self, model: usb_device, logger: logging.Logger, poll_interval_ns: int = 500, irq=None):
        self.ral = model
        self.irq = irq
        self.ram = usb_ram(model)
        self.logger = logger
        self.poll_interval_ns = poll_interval_ns
        self._events: Queue[HalEvent] = Queue()
        self._control = {"enable_pu": 0, "enable_core": 0}
        self._entries: dict[int, EntryConfig] = {}
        self._stalled: set[int] = set()
        self._poller = None

    # Register helpers ------------------------------------------------------------------------

    async def _write(self, register: uvm_reg, **fields: int) -> None:
        value = 0
        for name, field_value in fields.items():
            value |= int(field_value) << getattr(register, name).get_lsb_pos()
        status = await register.write(value)
        if status != uvm_status_e.UVM_IS_OK:
            raise RegisterAccessError(f"write of 0x{value:08X} to {register.get_full_name()} failed")

    async def _read(self, register: uvm_reg) -> int:
        status, value = await register.read()
        if status != uvm_status_e.UVM_IS_OK:
            raise RegisterAccessError(f"read of {register.get_full_name()} failed")
        return value

    @staticmethod
    def _field(register: uvm_reg, name: str, value: int) -> int:
        field = getattr(register, name)
        return (value >> field.get_lsb_pos()) & ((1 << field.get_n_bits()) - 1)

    async def _write_control(self, **pulses: int) -> None:
        await self._write(self.ral.control, **self._control, **pulses)

    async def _write_entry_control(self, entry: int) -> None:
        config = self._entries[entry]
        await self._write(self.ral.endpoint[entry].control,
                          base_address=config.base_address,
                          endpoint_number=config.endpoint_number,
                          stall=int(entry in self._stalled),
                          size=config.size_code,
                          kind=RDL_ENDPOINT_KIND[config.kind],
                          direction=1 if config.direction is Direction.OUT else 0,
                          enable=int(config.enable))

    # DeviceHal -------------------------------------------------------------------------------

    async def start(self) -> None:
        core_id = await self._read(self.ral.core_id)
        self.logger.info("USB core_id 0x%08X", core_id)
        self._control["enable_core"] = 1
        await self._write_control()
        await self._write(self.ral.bus_event_enable, bus_reset_en=1, suspend_en=1, resume_en=1)
        enables = {f"endpoint_{n}_enable": 1 for n in range(ENTRIES)}
        await self._write(self.ral.interrupt_enable, bus_event_enable=1, **enables)
        await self._clear_ram(0, EP0_BUFFER_BYTES)
        self._poller = cocotb.start_soon(self._poll())

    async def _clear_ram(self, base: int, length: int) -> None:
        """Firmware clears the buffers it reads back: unwritten RAM bytes are unknown ('U')
        in simulation, and an OUT read returns whole words."""
        for offset in range(length):
            await self.ram.write(base + offset, 0)

    def stop(self) -> None:
        if self._poller is not None:
            self._poller.cancel()
            self._poller = None

    async def next_event(self) -> HalEvent:
        return await self._events.get()

    async def configure_entry(self, entry: int, config: EntryConfig) -> None:
        self._entries[entry] = config
        self._stalled.discard(entry)
        await self._write_entry_control(entry)
        await self._write(self.ral.endpoint[entry].transfer, reset_toggle=1)
        setup = config.kind is TransferType.CONTROL and config.direction is Direction.OUT
        await self._write(self.ral.endpoint[entry].interrupt_enable,
                          transfer_complete_en=1, setup_en=int(setup))

    async def disable_entry(self, entry: int) -> None:
        if entry in self._entries and self._entries[entry].enable:
            config = self._entries[entry]
            self._entries[entry] = EntryConfig(config.endpoint_number, config.direction, config.kind,
                                               config.base_address, config.buffer_bytes, enable=False)
            self._stalled.discard(entry)
            await self._write_entry_control(entry)

    async def arm_in(self, entry: int, data: bytes) -> None:
        base = self._entries[entry].base_address
        for offset, byte in enumerate(data):
            status = await self.ram.write(base + offset, byte)
            if status != uvm_status_e.UVM_IS_OK:
                raise RegisterAccessError(f"usb_ram write at 0x{base + offset:03X} failed")
        await self._write(self.ral.endpoint[entry].transfer, arm=1, length=len(data))

    async def arm_out(self, entry: int) -> None:
        await self._write(self.ral.endpoint[entry].transfer, arm=1)

    async def set_stall(self, entry: int, stalled: bool) -> None:
        (self._stalled.add if stalled else self._stalled.discard)(entry)
        await self._write_entry_control(entry)

    async def reset_toggle(self, entry: int) -> None:
        await self._write(self.ral.endpoint[entry].transfer, reset_toggle=1)

    async def set_address(self, address: int) -> None:
        await self._write(self.ral.device_address, address=address)

    async def set_pull_up(self, enabled: bool) -> None:
        self._control["enable_pu"] = int(enabled)
        await self._write_control()

    async def signal_remote_wakeup(self) -> None:
        await self._write_control(remote_wakeup=1)

    # Event polling ---------------------------------------------------------------------------

    async def _poll(self) -> None:
        while True:
            pending = await self._read(self.ral.interrupt)
            if pending:
                await self._service(pending)
            elif self.irq is None:
                await Timer(self.poll_interval_ns, unit="ns")
            elif not int(self.irq.value):
                await RisingEdge(self.irq)

    async def _service(self, pending: int) -> None:
        if self._field(self.ral.interrupt, "bus_event", pending):
            await self._service_bus_events()
        for entry in range(ENTRIES):
            if self._field(self.ral.interrupt, f"endpoint_{entry}", pending):
                await self._service_entry(entry)

    async def _service_bus_events(self) -> None:
        register = self.ral.bus_event
        events = await self._read(register)
        await register.write(events)
        for name, event in (("suspend", Suspended()), ("resume", Resumed()), ("bus_reset", BusReset())):
            if self._field(register, name, events):
                if name == "bus_reset":
                    self._stalled.clear()
                self._events.put_nowait(event)

    async def _service_entry(self, entry: int) -> None:
        register = self.ral.endpoint[entry].interrupt
        causes = await self._read(register)
        await register.write(causes)
        if self._field(register, "overrun", causes):
            self.logger.debug("entry %d: OUT packet NAKed while not armed", entry)
        if self._field(register, "transfer_complete", causes):
            self._events.put_nowait(TransferDone(entry, await self._received_data(entry)))
        if self._field(register, "setup", causes):
            config = self._entries[entry]
            data = await self._read_buffer(config.base_address, SETUP_BYTES)
            self._stalled -= {e for e, c in self._entries.items() if c.endpoint_number == config.endpoint_number}
            self._events.put_nowait(SetupReceived(data))

    async def _received_data(self, entry: int) -> bytes:
        config = self._entries[entry]
        if config.direction is Direction.IN:
            return b""
        status = await self._read(self.ral.endpoint[entry].status)
        count = self._field(self.ral.endpoint[entry].status, "byte_count", status)
        return await self._read_buffer(config.base_address, count)

    async def _read_buffer(self, base: int, length: int) -> bytes:
        data = bytearray()
        for offset in range(length):
            status, byte = await self.ram.read(base + offset)
            if status != uvm_status_e.UVM_IS_OK:
                raise RegisterAccessError(f"usb_ram read at 0x{base + offset:03X} failed")
            data.append(byte)
        return bytes(data)
