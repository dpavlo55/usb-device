"""The base of every Chapter 9 test procedure (a virtual sequence).

It knows how USBCV puts a device into each state (USB 2.0 CV spec section 3.1):

* **Configured**: port reset, the "Enumerate Device Routine", then SET_CONFIGURATION of the
  configuration under test and GET_CONFIGURATION to verify it;
* **Address**: Configured, then SET_CONFIGURATION(0) and GET_CONFIGURATION == 0;
* **Default**: Address, then SET_ADDRESS(0).

Like USBCV, a procedure knows nothing about the device in advance: everything it checks
comes from what the device answers. Failures go to the compliance log tagged with the TD.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from pyuvm import uvm_sequence

from ..ch9.constants import DescriptorType, DeviceState, MAX_USB_ADDRESS
from ..ch9.descriptors import ConfigurationDescriptor, DeviceDescriptor
from ..common.compliance import ComplianceLog
from ..common.seeding import rng
from ..env.env_config import UsbEnvConfig
from .host import UsbHost
from .usb_seqs import ControlResult

ALL_STATES = (DeviceState.DEFAULT, DeviceState.ADDRESS, DeviceState.CONFIGURED)
ATTACH_TIMEOUT_FS = 20 * 1_000_000_000_000


class Ch9Aborted(Exception):
    """The procedure cannot continue (the device could not be put into a state)."""


@dataclass
class Enumeration:
    """What the enumeration routine learned about the device."""

    device: DeviceDescriptor
    device_bytes: bytes
    configurations: list[ConfigurationDescriptor] = field(default_factory=list)

    @property
    def bcd_usb(self) -> int:
        return self.device.bcd_usb


class Ch9Sequence(uvm_sequence):
    """Subclasses set ``td`` and implement :meth:`procedure`."""

    td = "TD 9.x"

    def __init__(self, name: str | None = None):
        super().__init__(name or type(self).__name__)
        self.enumeration: Enumeration | None = None

    async def body(self):
        vseqr = self.sequencer
        self.cfg: UsbEnvConfig = vseqr.cfg
        self.log: ComplianceLog = self.cfg.compliance
        self.device = vseqr.device
        self.host = UsbHost(vseqr.host, self.cfg.timing)
        self.rng = rng(self.cfg.seed, self.td)
        self._used_addresses: set[int] = set()
        self.info(f"start: {type(self).__doc__.strip().splitlines()[0] if type(self).__doc__ else ''}")
        try:
            await self.attach()
            await self.procedure()
        except Ch9Aborted as error:
            self.log.fail(self.td, f"aborted: {error}")
        self.info(f"done: {self.log.summary()}")

    async def procedure(self) -> None:
        raise NotImplementedError

    # Logging and checks ----------------------------------------------------------------------

    def info(self, message: str) -> None:
        self.log.info(self.td, message)

    def check(self, condition: bool, message: str) -> bool:
        return self.log.check(condition, self.td, message)

    def check_findings(self, findings: list[str], context: str) -> bool:
        self.log.checks += 1
        for finding in findings:
            self.log.fail(self.td, f"{context}: {finding}")
        return not findings

    def require(self, result: ControlResult, what: str) -> ControlResult:
        """A valid request that must succeed; the procedure cannot go on without it."""
        if not self.check(result.ok, f"{what} failed: {result}"):
            raise Ch9Aborted(f"{what} failed")
        return result

    def expect_request_error(self, result: ControlResult, what: str) -> bool:
        return self.check(result.stalled, f"{what} must be answered with STALL (request error), got: {result}")

    # Device bring-up -------------------------------------------------------------------------

    async def attach(self) -> None:
        if not await self.host.wait_for_attach(ATTACH_TIMEOUT_FS):
            raise Ch9Aborted("the device never connected its pull-up")

    def next_address(self) -> int:
        """A random address the device has not had yet in this procedure (USBCV: 'the next
        available USB address')."""
        free = [a for a in range(1, MAX_USB_ADDRESS + 1) if a not in self._used_addresses]
        address = self.rng.choice(free or list(range(1, MAX_USB_ADDRESS + 1)))
        self._used_addresses.add(address)
        return address

    async def enumerate(self) -> Enumeration:
        """The 'Enumerate Device Routine' (USB 2.0 CV spec, page 21; steps 1 to 7)."""
        host = self.host
        first = self.require(await host.get_device_descriptor(64), "GET_DESCRIPTOR(DEVICE, 64)")
        if len(first.data) >= 8:
            host.max_packet_size0 = first.data[7] or host.DEFAULT_MAX_PACKET_SIZE0
        self.require(await host.set_address(self.next_address()), "SET_ADDRESS")
        device = self.require(await host.get_device_descriptor(18), "GET_DESCRIPTOR(DEVICE, 18)")
        if len(device.data) != DeviceDescriptor.SIZE:
            raise Ch9Aborted(f"the device descriptor has {len(device.data)} bytes")
        enumeration = Enumeration(DeviceDescriptor.from_bytes(device.data), device.data)
        for index in range(max(enumeration.device.num_configurations, 1)):
            header = self.require(await host.get_configuration_descriptor(index, 9),
                                  f"GET_DESCRIPTOR(CONFIGURATION {index}, 9)")
            if len(header.data) != 9:
                raise Ch9Aborted(f"configuration {index} header has {len(header.data)} bytes")
            enumeration.configurations.append(ConfigurationDescriptor.from_bytes(header.data))
        first_value = enumeration.configurations[0].configuration_value
        self.require(await host.set_configuration(first_value), f"SET_CONFIGURATION({first_value})")
        self.require(await host.get_configuration_descriptor(0, 9), "GET_DESCRIPTOR(CONFIGURATION 0, 9)")
        total = enumeration.configurations[0].total_length
        self.require(await host.get_configuration_descriptor(0, total), f"GET_DESCRIPTOR(CONFIGURATION 0, {total})")
        self.enumeration = enumeration
        return enumeration

    async def enter_state(self, state: DeviceState, configuration_index: int = 0) -> Enumeration:
        """Put the device into ``state`` the way USBCV does, starting from a port reset."""
        host = self.host
        await host.bus_reset()
        enumeration = await self.enumerate()
        value = enumeration.configurations[configuration_index].configuration_value
        await self.set_and_verify_configuration(value)
        if state is not DeviceState.CONFIGURED:
            await self.set_and_verify_configuration(0)
        if state is DeviceState.DEFAULT:
            self.require(await host.set_address(0), "SET_ADDRESS(0)")
        self.info(f"device is in the {state.value} state (address {host.address})")
        return enumeration

    async def ensure_responding(self, state: DeviceState, after: str) -> None:
        """'Issue a GetDescriptor(Device) request to make sure the device is still responding;
        if this fails, re-enumerate the device' (TD 9.10 / 9.11 steps 4-5 and 8-9)."""
        if self.check((await self.host.get_device_descriptor(18)).ok,
                      f"the device does not answer GET_DESCRIPTOR(DEVICE) after {after}"):
            return
        await self.enter_state(state)

    async def set_and_verify_configuration(self, value: int) -> None:
        self.require(await self.host.set_configuration(value), f"SET_CONFIGURATION({value})")
        result, reported = await self.host.get_configuration()
        self.require(result, "GET_CONFIGURATION")
        if not self.check(reported == value, f"GET_CONFIGURATION returned {reported}, expected {value}"):
            raise Ch9Aborted("configuration not reported as set")

    async def for_each_state(self, body: Callable[[DeviceState, int], Awaitable[None]],
                             states: tuple[DeviceState, ...] = ALL_STATES,
                             each_configuration: bool = True) -> None:
        """Run ``body(state, configuration_index)`` in each state (USBCV: 'repeat in the default,
        address, and configured states' and 'for each configuration')."""
        if self.enumeration is None:
            await self.enter_state(DeviceState.CONFIGURED)
        count = len(self.enumeration.configurations) if each_configuration else 1
        for state in states:
            indices = range(count) if state is DeviceState.CONFIGURED else range(1)
            for index in indices:
                self.info(f"--- {state.value} state, configuration index {index}")
                await self.enter_state(state, index)
                await body(state, index)

    async def read_configuration(self, index: int, other_speed: bool = False) -> bytes | None:
        """Header (9 bytes) then the full set (wTotalLength), as every descriptor test does."""
        descriptor_type = DescriptorType.OTHER_SPEED_CONFIGURATION if other_speed else DescriptorType.CONFIGURATION
        name = f"GET_DESCRIPTOR({descriptor_type.name} {index}"
        header = self.require(await self.host.get_descriptor(descriptor_type, index, 9), f"{name}, 9)")
        if len(header.data) < 4:
            self.check(False, f"{name}, 9) returned {len(header.data)} bytes")
            return None
        total = int.from_bytes(header.data[2:4], "little")
        full = self.require(await self.host.get_descriptor(descriptor_type, index, total), f"{name}, {total})")
        return full.data
