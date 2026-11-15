"""Chapter 9 state tracking from the bus alone (a black-box reference model).

From the control transfers that complete on the wire, the tracker follows what the USB 2.0
specification says the device state must be (figure 9-1 and section 9.4): address,
configuration, alternate settings, halted endpoints and the remote-wakeup feature. It
learns the endpoints from the configuration descriptors the device itself returns. Every
answer that depends on state is checked, in every test:

* GET_CONFIGURATION returns the configuration set last (0 outside the Configured state);
* GET_INTERFACE returns the alternate setting selected last;
* GET_STATUS reports the remote-wakeup feature and endpoint halt as last set or cleared;
* GET_INTERFACE, SET_INTERFACE and interface/endpoint GET_STATUS are request errors in the
  Address state (section 9.4);
* a GET_DESCRIPTOR answer is never longer than wLength.

Each transfer is published on ``annotated_ap`` with the state it was issued in, for
coverage.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from pyuvm import ConfigDB, uvm_analysis_port, uvm_component

from ..ch9.constants import (
    DescriptorType,
    DeviceState,
    DeviceStatus,
    EndpointStatus,
    FeatureSelector,
    Recipient,
    Request,
)
from ..ch9.descriptors import DescriptorError
from ..ch9.usbcv import ParsedConfiguration, parse_configuration
from ..common.compliance import ComplianceLog
from ..common.reset import ResetAware
from ..common.tlm import analysis_imp
from ..usb.line import LineCondition, LineEvent
from ..usb.transfers import ControlOutcome, ObservedControlTransfer

SOURCE = "ch9-state"


@dataclass(frozen=True)
class AnnotatedTransfer:
    transfer: ObservedControlTransfer
    state: DeviceState
    max_packet_size0: int | None


@dataclass
class TrackedDevice:
    state: DeviceState = DeviceState.DEFAULT
    address: int = 0
    configuration: int = 0
    alternates: dict[int, int] = field(default_factory=dict)
    halted: set[int] = field(default_factory=set)
    remote_wakeup: bool = False


class Ch9StateTracker(ResetAware, uvm_component):
    def build_phase(self):
        super().build_phase()
        self.log: ComplianceLog = ConfigDB().get(self, "", "compliance")
        self.control_export = analysis_imp("control_export", self, self.write)
        self.line_event_export = analysis_imp("line_event_export", self, self.write_line_event)
        self.annotated_ap = uvm_analysis_port("annotated_ap", self)
        self.configurations: dict[int, ParsedConfiguration] = {}
        self.max_packet_size0: int | None = None
        self.on_reset()

    def on_reset(self) -> None:
        self.device = TrackedDevice(state=DeviceState.DETACHED)

    def write_line_event(self, event: LineEvent) -> None:
        if event.condition is LineCondition.RESET:
            self.device = TrackedDevice()
        elif event.condition is LineCondition.DETACH:
            self.device = TrackedDevice(state=DeviceState.DETACHED)

    def write(self, transfer: ObservedControlTransfer) -> None:
        state = self.device.state
        self.annotated_ap.write(AnnotatedTransfer(transfer, state, self.max_packet_size0))
        if transfer.address != self.device.address:
            return
        setup = transfer.setup
        if transfer.outcome is ControlOutcome.COMPLETED:
            self._check_answer(transfer)
            self._learn(transfer)
            self._update(transfer)
        if state is DeviceState.ADDRESS and transfer.outcome is not ControlOutcome.ABORTED:
            self._check_address_state_errors(transfer)
        if setup.request is Request.GET_DESCRIPTOR and transfer.data:
            self.log.check(len(transfer.data) <= setup.w_length, SOURCE,
                           f"{setup}: {len(transfer.data)} bytes returned, more than wLength")

    # Checks ----------------------------------------------------------------------------------

    def _check_answer(self, transfer: ObservedControlTransfer) -> None:
        setup, data, device = transfer.setup, transfer.data, self.device
        match setup.request, setup.recipient:
            case Request.GET_CONFIGURATION, _ if len(data) == 1:
                self.log.check(data[0] == device.configuration, SOURCE,
                               f"GET_CONFIGURATION returned {data[0]}, configuration {device.configuration} is set")
            case Request.GET_INTERFACE, _ if len(data) == 1 and setup.w_index in device.alternates:
                expected = device.alternates[setup.w_index]
                self.log.check(data[0] == expected, SOURCE,
                               f"GET_INTERFACE({setup.w_index}) returned {data[0]}, alternate {expected} is selected")
            case Request.GET_STATUS, Recipient.DEVICE if len(data) == 2:
                reported = bool(int.from_bytes(data, "little") & DeviceStatus.REMOTE_WAKEUP)
                self.log.check(reported == device.remote_wakeup, SOURCE,
                               f"GET_STATUS(device) reports remote wakeup {reported}, expected {device.remote_wakeup}")
            case Request.GET_STATUS, Recipient.ENDPOINT if len(data) == 2 and setup.w_index & 0x7F:
                halted = bool(int.from_bytes(data, "little") & EndpointStatus.HALT)
                expected = setup.w_index in device.halted
                self.log.check(halted == expected, SOURCE,
                               f"GET_STATUS(endpoint 0x{setup.w_index:02X}) reports halt {halted}, expected {expected}")

    def _check_address_state_errors(self, transfer: ObservedControlTransfer) -> None:
        setup = transfer.setup
        must_fail = (setup.request in (Request.GET_INTERFACE, Request.SET_INTERFACE)
                     or (setup.request is Request.GET_STATUS and setup.recipient is Recipient.INTERFACE)
                     or (setup.request is Request.GET_STATUS and setup.recipient is Recipient.ENDPOINT
                         and setup.w_index & 0x7F))
        if must_fail:
            self.log.check(transfer.outcome is ControlOutcome.STALLED, SOURCE,
                           f"{setup} in the Address state must be a request error (STALL)")

    # State -----------------------------------------------------------------------------------

    def _learn(self, transfer: ObservedControlTransfer) -> None:
        """Remember what the device says about itself."""
        setup, data = transfer.setup, transfer.data
        if setup.request is not Request.GET_DESCRIPTOR:
            return
        if setup.descriptor_type == DescriptorType.DEVICE and len(data) >= 8:
            self.max_packet_size0 = data[7]
        if setup.descriptor_type == DescriptorType.CONFIGURATION and len(data) > 9:
            try:
                parsed = parse_configuration(data)
            except DescriptorError:
                return
            if parsed.header.total_length == len(data):
                self.configurations[parsed.header.configuration_value] = parsed

    def _update(self, transfer: ObservedControlTransfer) -> None:
        setup, device = transfer.setup, self.device
        match setup.request:
            case Request.SET_ADDRESS:
                device.address = setup.w_value
                if device.state is not DeviceState.CONFIGURED:
                    device.state = DeviceState.ADDRESS if setup.w_value else DeviceState.DEFAULT
            case Request.SET_CONFIGURATION:
                device.configuration = setup.w_value
                unconfigured = DeviceState.ADDRESS if device.address else DeviceState.DEFAULT
                device.state = DeviceState.CONFIGURED if setup.w_value else unconfigured
                device.halted.clear()
                parsed = self.configurations.get(setup.w_value)
                device.alternates = {i.interface_number: 0 for i in parsed.interfaces} if parsed else {}
            case Request.SET_INTERFACE:
                device.alternates[setup.w_index] = setup.w_value
                parsed = self.configurations.get(device.configuration)
                if parsed:
                    device.halted -= {ep.endpoint_address for i, ep in parsed.endpoints
                                      if i.interface_number == setup.w_index}
            case Request.SET_FEATURE | Request.CLEAR_FEATURE:
                enable = setup.request is Request.SET_FEATURE
                if setup.recipient is Recipient.DEVICE and setup.w_value == FeatureSelector.DEVICE_REMOTE_WAKEUP:
                    device.remote_wakeup = enable
                elif setup.recipient is Recipient.ENDPOINT and setup.w_value == FeatureSelector.ENDPOINT_HALT:
                    (device.halted.add if enable else device.halted.discard)(setup.w_index)

    def endpoint_type(self, address: int):
        parsed = self.configurations.get(self.device.configuration)
        for _, endpoint in parsed.endpoints if parsed else ():
            if endpoint.endpoint_address == address:
                return endpoint.transfer_type
        return None

