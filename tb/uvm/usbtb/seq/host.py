"""The host software stack, as a thin facade over the host sequences.

Virtual sequences read like the USBCV procedures with it::

    result = await host.get_descriptor(DescriptorType.DEVICE, length=18)
    await host.set_address(42)

Each call starts the matching sequence on the host sequencer; the facade only remembers
what a host remembers about its device: the address and endpoint 0's packet size.
"""
from __future__ import annotations

from pyuvm import uvm_sequencer

from ..ch9.constants import DescriptorType, FeatureSelector, Recipient
from ..ch9.setup import SetupPacket
from ..usb.host_agent import BusOp, UsbTransactionItem
from ..usb.timing import UsbTiming
from ..usb.wire import Pid
from .usb_seqs import ControlResult, ControlTransferSeq, UsbBusSeq, UsbTransactionSeq


class UsbHost:
    DEFAULT_MAX_PACKET_SIZE0 = 64

    def __init__(self, sequencer: uvm_sequencer, timing: UsbTiming):
        self.sequencer = sequencer
        self.timing = timing
        self.address = 0
        self.max_packet_size0 = self.DEFAULT_MAX_PACKET_SIZE0

    # Control transfers -------------------------------------------------------------------------

    async def control(self, setup: SetupPacket, address: int | None = None) -> ControlResult:
        seq = ControlTransferSeq(self.address if address is None else address, setup, self.timing,
                                 self.max_packet_size0)
        await seq.start(self.sequencer)
        return seq.result

    async def get_descriptor(self, descriptor_type: int, index: int = 0, length: int = 0xFF,
                             language_id: int = 0) -> ControlResult:
        return await self.control(SetupPacket.get_descriptor(descriptor_type, index, length, language_id))

    async def get_device_descriptor(self, length: int = 18) -> ControlResult:
        return await self.get_descriptor(DescriptorType.DEVICE, length=length)

    async def get_configuration_descriptor(self, index: int, length: int) -> ControlResult:
        return await self.get_descriptor(DescriptorType.CONFIGURATION, index, length)

    async def set_address(self, address: int) -> ControlResult:
        result = await self.control(SetupPacket.set_address(address))
        if result.ok:
            self.address = address
            await self.idle(self.timing.set_address_recovery_fs)
        return result

    async def get_configuration(self) -> tuple[ControlResult, int | None]:
        result = await self.control(SetupPacket.get_configuration())
        return result, result.data[0] if result.ok and len(result.data) == 1 else None

    async def set_configuration(self, value: int) -> ControlResult:
        return await self.control(SetupPacket.set_configuration(value))

    async def get_interface(self, interface: int) -> tuple[ControlResult, int | None]:
        result = await self.control(SetupPacket.get_interface(interface))
        return result, result.data[0] if result.ok and len(result.data) == 1 else None

    async def set_interface(self, interface: int, alternate: int) -> ControlResult:
        return await self.control(SetupPacket.set_interface(interface, alternate))

    async def get_status(self, recipient: Recipient = Recipient.DEVICE, index: int = 0) -> tuple[ControlResult, int | None]:
        result = await self.control(SetupPacket.get_status(recipient, index))
        value = int.from_bytes(result.data, "little") if result.ok and len(result.data) == 2 else None
        return result, value

    async def set_feature(self, selector: FeatureSelector | int, recipient: Recipient = Recipient.DEVICE,
                          index: int = 0) -> ControlResult:
        return await self.control(SetupPacket.set_feature(selector, recipient, index))

    async def clear_feature(self, selector: FeatureSelector | int, recipient: Recipient = Recipient.DEVICE,
                            index: int = 0) -> ControlResult:
        return await self.control(SetupPacket.clear_feature(selector, recipient, index))

    # Single transactions and bus states --------------------------------------------------------

    async def transaction(self, kind: Pid, endpoint: int, payload: bytes = b"", data_pid: Pid = Pid.DATA0,
                          address: int | None = None) -> UsbTransactionItem:
        item = UsbTransactionItem(kind.name.lower(), kind, self.address if address is None else address,
                                  endpoint, payload, data_pid if kind is not Pid.IN else None)
        await UsbTransactionSeq(item).start(self.sequencer)
        return item

    async def _bus(self, op: BusOp, duration_fs: int = 0) -> bool:
        seq = UsbBusSeq(op, duration_fs)
        await seq.start(self.sequencer)
        return seq.item.happened

    async def bus_reset(self) -> None:
        """Port reset, then reset recovery: the device is in the Default state at address 0."""
        await self._bus(BusOp.RESET, self.timing.bus_reset_fs)
        self.address = 0
        self.max_packet_size0 = self.DEFAULT_MAX_PACKET_SIZE0
        await self.idle(self.timing.reset_recovery_fs)

    async def wait_for_attach(self, timeout_fs: int) -> bool:
        attached = await self._bus(BusOp.WAIT_ATTACH, timeout_fs)
        if attached:
            await self.idle(self.timing.attach_debounce_fs)
        return attached

    async def suspend(self, duration_fs: int | None = None) -> None:
        await self._bus(BusOp.SUSPEND, self.timing.suspend_fs if duration_fs is None else duration_fs)

    async def resume(self) -> None:
        await self._bus(BusOp.RESUME, self.timing.resume_fs)
        await self.idle(self.timing.resume_recovery_fs)

    async def wait_for_remote_wakeup(self, timeout_fs: int | None = None) -> bool:
        """Wait (while suspended) for the device's resume signaling; the host then resumes."""
        woke = await self._bus(BusOp.WAIT_REMOTE_WAKEUP,
                               self.timing.remote_wakeup_timeout_fs if timeout_fs is None else timeout_fs)
        if woke:
            await self.idle(self.timing.resume_recovery_fs)
        return woke

    async def idle(self, duration_fs: int) -> None:
        await self._bus(BusOp.IDLE, duration_fs)
