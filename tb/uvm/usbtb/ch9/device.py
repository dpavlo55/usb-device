"""The Chapter 9 part of the device software: standard requests and the device state.

:class:`Ch9Device` answers one SETUP packet at a time. It returns what the control
transfer must do (send data, accept, or STALL) and the hardware actions that go with it;
the control-transfer engine in :mod:`usbtb.device.software` carries those out through
whichever hardware abstraction layer it runs on. Nothing here touches hardware.
"""
from __future__ import annotations

import enum
import struct
from dataclasses import dataclass, field

from .constants import (
    DescriptorType,
    DeviceState,
    DeviceStatus,
    Direction,
    EndpointStatus,
    FeatureSelector,
    MAX_USB_ADDRESS,
    Recipient,
    Request,
    RequestType,
)
from .descriptors import Configuration, DescriptorSet, EndpointDescriptor
from .setup import SetupPacket


# Hardware actions requested by a standard request ------------------------------------------

@dataclass(frozen=True, slots=True)
class SetAddress:
    """Write the new device address; only after the status stage has completed."""

    address: int


@dataclass(frozen=True, slots=True)
class Configure:
    """Program the endpoints of a configuration (``None``: unconfigure), toggles to DATA0."""

    configuration: Configuration | None


@dataclass(frozen=True, slots=True)
class SelectAlternate:
    configuration: Configuration
    interface: int
    alternate: int


@dataclass(frozen=True, slots=True)
class HaltEndpoint:
    endpoint: EndpointDescriptor
    halted: bool


Action = SetAddress | Configure | SelectAlternate | HaltEndpoint


@dataclass(frozen=True, slots=True)
class ControlResponse:
    """How to finish a control transfer."""

    data: bytes | None = None
    stall: bool = False
    actions: tuple[Action, ...] = ()

    @classmethod
    def request_error(cls) -> ControlResponse:
        return cls(stall=True)


class SoftwareFault(enum.Enum):
    """Deliberate device-software bugs, used only to prove the checkers catch them."""

    NO_STALL_ON_BAD_REQUEST = "answers unsupported requests instead of stalling"
    BAD_DEVICE_DESCRIPTOR_LENGTH = "reports bLength 17 in the device descriptor"
    WAKEUP_WHEN_DISABLED = "signals remote wakeup although the host disabled it"


@dataclass
class Ch9Device:
    descriptors: DescriptorSet
    faults: frozenset[SoftwareFault] = frozenset()
    state: DeviceState = DeviceState.DEFAULT
    address: int = 0
    configuration: Configuration | None = None
    alternates: dict[int, int] = field(default_factory=dict)
    halted: set[int] = field(default_factory=set)
    remote_wakeup_enabled: bool = False

    def bus_reset(self) -> None:
        """USB 2.0 section 9.1.1.3: a reset returns the device to the Default state."""
        self.state = DeviceState.DEFAULT
        self.address = 0
        self._unconfigure()
        self.remote_wakeup_enabled = False

    @property
    def remote_wakeup_allowed(self) -> bool:
        return self.remote_wakeup_enabled or SoftwareFault.WAKEUP_WHEN_DISABLED in self.faults

    def handle(self, setup: SetupPacket) -> ControlResponse:
        if setup.type is not RequestType.STANDARD:
            return self._error()
        handler = self._HANDLERS.get(setup.request)
        response = handler(self, setup) if handler else self._error()
        if response.data is not None:
            response = ControlResponse(response.data[: setup.w_length], response.stall, response.actions)
        return response

    def complete(self, action: Action) -> None:
        """Bookkeeping once the hardware has carried out ``action``."""
        match action:
            case SetAddress(address=address):
                self.address = address
                if self.state is not DeviceState.CONFIGURED:
                    self.state = DeviceState.ADDRESS if address else DeviceState.DEFAULT

    # Requests ------------------------------------------------------------------------------

    def _error(self) -> ControlResponse:
        if SoftwareFault.NO_STALL_ON_BAD_REQUEST in self.faults:
            return ControlResponse(data=b"")
        return ControlResponse.request_error()

    def _get_status(self, setup: SetupPacket) -> ControlResponse:
        if setup.direction is not Direction.IN or setup.w_value != 0 or setup.w_length != 2:
            return self._error()
        match setup.recipient:
            case Recipient.DEVICE:
                status = DeviceStatus(0)
                if self._self_powered:
                    status |= DeviceStatus.SELF_POWERED
                if self.remote_wakeup_enabled:
                    status |= DeviceStatus.REMOTE_WAKEUP
                return ControlResponse(data=struct.pack("<H", status))
            case Recipient.INTERFACE if self._interface_exists(setup.w_index):
                return ControlResponse(data=b"\x00\x00")
            case Recipient.ENDPOINT if (setup.w_index & 0x7F) == 0:
                return ControlResponse(data=b"\x00\x00")
            case Recipient.ENDPOINT if self._endpoint(setup.w_index) is not None:
                halted = setup.w_index in self.halted
                return ControlResponse(data=struct.pack("<H", EndpointStatus.HALT if halted else 0))
        return self._error()

    def _feature(self, setup: SetupPacket) -> ControlResponse:
        if setup.direction is not Direction.OUT or setup.w_length != 0:
            return self._error()
        enable = setup.request is Request.SET_FEATURE
        match setup.recipient, setup.w_value:
            case Recipient.DEVICE, FeatureSelector.DEVICE_REMOTE_WAKEUP:
                self.remote_wakeup_enabled = enable
                return ControlResponse()
            case Recipient.ENDPOINT, FeatureSelector.ENDPOINT_HALT:
                endpoint = self._endpoint(setup.w_index)
                if endpoint is None:
                    return self._error()
                (self.halted.add if enable else self.halted.discard)(setup.w_index)
                return ControlResponse(actions=(HaltEndpoint(endpoint, enable),))
        # TEST_MODE is a high-speed feature; anything else is undefined: request error.
        return self._error()

    def _set_address(self, setup: SetupPacket) -> ControlResponse:
        if (setup.recipient is not Recipient.DEVICE or setup.w_value > MAX_USB_ADDRESS
                or setup.w_index or setup.w_length):
            return self._error()
        return ControlResponse(actions=(SetAddress(setup.w_value),))

    def _get_descriptor(self, setup: SetupPacket) -> ControlResponse:
        if setup.direction is not Direction.IN or setup.recipient is not Recipient.DEVICE:
            return self._error()
        data = self.descriptors.descriptor_bytes(setup.descriptor_type, setup.descriptor_index,
                                                 setup.w_index)
        if data is None:
            return self._error()
        if (setup.descriptor_type == DescriptorType.DEVICE
                and SoftwareFault.BAD_DEVICE_DESCRIPTOR_LENGTH in self.faults):
            data = bytes((17,)) + data[1:]
        return ControlResponse(data=data)

    def _get_configuration(self, setup: SetupPacket) -> ControlResponse:
        if setup.direction is not Direction.IN or setup.w_length != 1:
            return self._error()
        value = self.configuration.configuration_value if self.configuration else 0
        return ControlResponse(data=bytes((value,)))

    def _set_configuration(self, setup: SetupPacket) -> ControlResponse:
        # USB 2.0 leaves SET_CONFIGURATION in the Default state unspecified; USBCV issues it
        # there (TD 9.4/9.5/9.9 step 5), so it is accepted like in the Address state.
        if setup.w_length or setup.w_index:
            return self._error()
        if setup.w_value == 0:
            self._unconfigure()
            self.state = DeviceState.ADDRESS if self.address else DeviceState.DEFAULT
            return ControlResponse(actions=(Configure(None),))
        configuration = self.descriptors.configuration(setup.w_value)
        if configuration is None:
            return self._error()
        self._unconfigure()
        self.configuration = configuration
        self.alternates = {i.descriptor.interface_number: 0 for i in configuration.interfaces}
        self.state = DeviceState.CONFIGURED
        return ControlResponse(actions=(Configure(configuration),))

    def _get_interface(self, setup: SetupPacket) -> ControlResponse:
        if setup.direction is not Direction.IN or setup.w_length != 1 or not self._interface_exists(setup.w_index):
            return self._error()
        return ControlResponse(data=bytes((self.alternates[setup.w_index],)))

    def _set_interface(self, setup: SetupPacket) -> ControlResponse:
        interface, alternate = setup.w_index, setup.w_value
        if setup.w_length or not self._interface_exists(interface):
            return self._error()
        if not any(i.descriptor.alternate_setting == alternate for i in self.configuration.alternates(interface)):
            return self._error()
        self.alternates[interface] = alternate
        for alt in self.configuration.alternates(interface):
            for endpoint in alt.endpoints:
                self.halted.discard(endpoint.endpoint_address)
        return ControlResponse(actions=(SelectAlternate(self.configuration, interface, alternate),))

    _HANDLERS = {
        Request.GET_STATUS: _get_status,
        Request.CLEAR_FEATURE: _feature,
        Request.SET_FEATURE: _feature,
        Request.SET_ADDRESS: _set_address,
        Request.GET_DESCRIPTOR: _get_descriptor,
        Request.GET_CONFIGURATION: _get_configuration,
        Request.SET_CONFIGURATION: _set_configuration,
        Request.GET_INTERFACE: _get_interface,
        Request.SET_INTERFACE: _set_interface,
    }

    # Helpers -------------------------------------------------------------------------------

    def _unconfigure(self) -> None:
        self.configuration = None
        self.alternates = {}
        self.halted.clear()

    @property
    def _self_powered(self) -> bool:
        configuration = self.configuration or self.descriptors.configurations[0]
        return configuration.header().self_powered

    def _interface_exists(self, number: int) -> bool:
        return self.state is DeviceState.CONFIGURED and number in self.alternates

    def _endpoint(self, address: int) -> EndpointDescriptor | None:
        """An endpoint of the active alternate settings; ``None`` outside the Configured state."""
        if self.state is not DeviceState.CONFIGURED or self.configuration is None:
            return None
        for interface in self.configuration.interfaces:
            descriptor = interface.descriptor
            if self.alternates.get(descriptor.interface_number) != descriptor.alternate_setting:
                continue
            for endpoint in interface.endpoints:
                if endpoint.endpoint_address == address:
                    return endpoint
        return None
