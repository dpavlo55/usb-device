"""Functional coverage of the Chapter 9 tests, with pyvsc covergroups.

What is sampled, and why it matters:

* every standard request, in every device state, with its outcome (ACK or request error);
* descriptor reads by type, how the answer relates to wLength, how many packets it took and
  whether it needed a zero-length packet to end;
* feature requests by selector, recipient and outcome;
* the device addresses assigned, the NAKs the host saw per control transfer;
* bus events: reset, suspend, host resume, device remote wakeup;
* the shape of the randomized device profile itself.

pyvsc keeps covergroup types in a global registry, so coverage accumulates over all tests
of one simulation run; the report and a UCIS XML database are written at the end of each
test.
"""
from __future__ import annotations

import enum
from pathlib import Path

import vsc
from pyuvm import ConfigDB, uvm_component

from ..ch9.constants import (
    DescriptorType,
    DeviceState,
    FeatureSelector,
    Recipient,
    Request,
    TransferType,
)
from ..ch9.descriptors import DescriptorSet
from ..common.tlm import analysis_imp
from ..usb.line import LineCondition, LineEvent, Source
from .ch9_tracker import AnnotatedTransfer
from ..usb.transfers import ControlOutcome

INVALID = 0xFF


class DescriptorKind(enum.IntEnum):
    DEVICE = DescriptorType.DEVICE
    CONFIGURATION = DescriptorType.CONFIGURATION
    STRING = DescriptorType.STRING
    DEVICE_QUALIFIER = DescriptorType.DEVICE_QUALIFIER
    OTHER_SPEED = DescriptorType.OTHER_SPEED_CONFIGURATION
    BOS = DescriptorType.BOS
    UNDEFINED = INVALID


class ReplyLength(enum.IntEnum):
    SHORTER_THAN_REQUESTED = 0
    AS_REQUESTED = 1


class BusEvent(enum.IntEnum):
    BUS_RESET = 0
    SUSPEND = 1
    HOST_RESUME = 2
    REMOTE_WAKEUP = 3


STATES = {DeviceState.DEFAULT: 0, DeviceState.ADDRESS: 1, DeviceState.CONFIGURED: 2}


@vsc.covergroup
class StandardRequestCoverage:
    def __init__(self):
        self.with_sample(dict(request=vsc.uint8_t(), state=vsc.uint8_t(), stalled=vsc.bit_t(1)))
        self.cp_request = vsc.coverpoint(self.request, bins={
            r.name: vsc.bin(int(r)) for r in Request if r not in (Request.SET_DESCRIPTOR, Request.SYNCH_FRAME)})
        self.cp_state = vsc.coverpoint(self.state, bins={s.name: vsc.bin(v) for s, v in STATES.items()})
        self.cp_outcome = vsc.coverpoint(self.stalled, bins={"acked": vsc.bin(0), "request_error": vsc.bin(1)})
        self.request_x_state = vsc.cross([self.cp_request, self.cp_state])
        self.request_x_outcome = vsc.cross([self.cp_request, self.cp_outcome])


@vsc.covergroup
class DescriptorReadCoverage:
    def __init__(self):
        self.with_sample(dict(kind=vsc.uint8_t(), reply=vsc.uint8_t(), packets=vsc.uint8_t(),
                              zlp=vsc.bit_t(1), state=vsc.uint8_t()))
        self.cp_kind = vsc.coverpoint(self.kind, bins={k.name: vsc.bin(int(k)) for k in DescriptorKind})
        self.cp_reply = vsc.coverpoint(self.reply, bins={r.name: vsc.bin(int(r)) for r in ReplyLength})
        self.cp_packets = vsc.coverpoint(self.packets, bins={
            "one": vsc.bin(1), "two_or_three": vsc.bin([2, 3]), "four_or_more": vsc.bin([4, 255])})
        self.cp_zlp = vsc.coverpoint(self.zlp, bins={"no_zlp": vsc.bin(0), "zlp_terminated": vsc.bin(1)})
        self.cp_state = vsc.coverpoint(self.state, bins={s.name: vsc.bin(v) for s, v in STATES.items()})
        self.kind_x_state = vsc.cross([self.cp_kind, self.cp_state])


@vsc.covergroup
class FeatureCoverage:
    def __init__(self):
        self.with_sample(dict(selector=vsc.uint8_t(), recipient=vsc.uint8_t(), set=vsc.bit_t(1),
                              stalled=vsc.bit_t(1), endpoint_type=vsc.uint8_t()))
        self.cp_selector = vsc.coverpoint(self.selector, bins={
            "endpoint_halt": vsc.bin(FeatureSelector.ENDPOINT_HALT),
            "remote_wakeup": vsc.bin(FeatureSelector.DEVICE_REMOTE_WAKEUP),
            "undefined": vsc.bin([3, 255])})
        self.cp_recipient = vsc.coverpoint(self.recipient, bins={
            "device": vsc.bin(Recipient.DEVICE), "endpoint": vsc.bin(Recipient.ENDPOINT)})
        self.cp_set = vsc.coverpoint(self.set, bins={"clear": vsc.bin(0), "set": vsc.bin(1)})
        self.cp_outcome = vsc.coverpoint(self.stalled, bins={"acked": vsc.bin(0), "request_error": vsc.bin(1)})
        self.cp_endpoint_type = vsc.coverpoint(self.endpoint_type, bins={
            "bulk": vsc.bin(TransferType.BULK), "interrupt": vsc.bin(TransferType.INTERRUPT)})
        self.selector_x_set_x_outcome = vsc.cross([self.cp_selector, self.cp_set, self.cp_outcome])


@vsc.covergroup
class AddressAndNakCoverage:
    def __init__(self):
        self.with_sample(dict(address=vsc.uint8_t(), naks=vsc.uint16_t()))
        self.cp_address = vsc.coverpoint(self.address, bins={
            "default": vsc.bin(0), "one": vsc.bin(1), "low": vsc.bin([2, 63]),
            "high": vsc.bin([64, 126]), "max": vsc.bin(127)})
        self.cp_naks = vsc.coverpoint(self.naks, bins={
            "none": vsc.bin(0), "few": vsc.bin([1, 3]), "several": vsc.bin([4, 15]), "many": vsc.bin([16, 65535])})


@vsc.covergroup
class BusEventCoverage:
    def __init__(self):
        self.with_sample(dict(event=vsc.uint8_t()))
        self.cp_event = vsc.coverpoint(self.event, bins={e.name: vsc.bin(int(e)) for e in BusEvent})


@vsc.covergroup
class DeviceProfileCoverage:
    def __init__(self):
        self.with_sample(dict(max_packet_size0=vsc.uint8_t(), configurations=vsc.uint8_t(),
                              has_iad=vsc.bit_t(1), has_alternates=vsc.bit_t(1), has_isochronous=vsc.bit_t(1),
                              remote_wakeup=vsc.bit_t(1), self_powered=vsc.bit_t(1), usb_2_0=vsc.bit_t(1)))
        self.cp_mps0 = vsc.coverpoint(self.max_packet_size0, bins={f"mps{v}": vsc.bin(v) for v in (8, 16, 32, 64)})
        self.cp_configurations = vsc.coverpoint(self.configurations, bins={
            "one": vsc.bin(1), "two": vsc.bin(2), "three": vsc.bin(3)})
        self.cp_iad = vsc.coverpoint(self.has_iad, bins={"no": vsc.bin(0), "yes": vsc.bin(1)})
        self.cp_alternates = vsc.coverpoint(self.has_alternates, bins={"no": vsc.bin(0), "yes": vsc.bin(1)})
        self.cp_isochronous = vsc.coverpoint(self.has_isochronous, bins={"no": vsc.bin(0), "yes": vsc.bin(1)})
        self.cp_remote_wakeup = vsc.coverpoint(self.remote_wakeup, bins={"no": vsc.bin(0), "yes": vsc.bin(1)})
        self.cp_self_powered = vsc.coverpoint(self.self_powered, bins={"bus": vsc.bin(0), "self": vsc.bin(1)})
        self.cp_bcd_usb = vsc.coverpoint(self.usb_2_0, bins={"usb_1_1": vsc.bin(0), "usb_2_0": vsc.bin(1)})


def _descriptor_kind(descriptor_type: int) -> DescriptorKind:
    return DescriptorKind(descriptor_type) if descriptor_type in DescriptorKind._value2member_map_ else DescriptorKind.UNDEFINED


class Ch9Coverage(uvm_component):
    def build_phase(self):
        self.control_export = analysis_imp("control_export", self, self.sample_transfer)
        self.line_event_export = analysis_imp("line_event_export", self, self.sample_line_event)
        self.output_dir = Path(ConfigDB().get(self, "", "coverage_dir"))
        self.requests = StandardRequestCoverage()
        self.descriptors = DescriptorReadCoverage()
        self.features = FeatureCoverage()
        self.addresses = AddressAndNakCoverage()
        self.bus = BusEventCoverage()
        self.profile = DeviceProfileCoverage()
        self.tracker = None

    def sample_profile(self, descriptors: DescriptorSet) -> None:
        configurations = descriptors.configurations
        endpoints = [ep for c in configurations for ep in c.endpoints]
        self.profile.sample(
            descriptors.device.max_packet_size0, len(configurations),
            int(any(c.associations for c in configurations)),
            int(any(i.descriptor.alternate_setting for c in configurations for i in c.interfaces)),
            int(any(ep.transfer_type is TransferType.ISOCHRONOUS for ep in endpoints)),
            int(any(c.header().remote_wakeup for c in configurations)),
            int(any(c.header().self_powered for c in configurations)),
            int(descriptors.device.bcd_usb >= 0x0200))

    def sample_transfer(self, annotated: AnnotatedTransfer) -> None:
        transfer, setup = annotated.transfer, annotated.transfer.setup
        if transfer.outcome is ControlOutcome.ABORTED or annotated.state not in STATES:
            return
        state = STATES[annotated.state]
        stalled = int(transfer.outcome is ControlOutcome.STALLED)
        if isinstance(setup.request, Request):
            self.requests.sample(int(setup.request), state, stalled)
        self.addresses.sample(transfer.address, transfer.naks)
        if setup.request is Request.GET_DESCRIPTOR:
            mps0 = annotated.max_packet_size0 or 64
            data = transfer.data
            zlp = int(bool(data) and len(data) % mps0 == 0 and len(data) < setup.w_length)
            reply = ReplyLength.SHORTER_THAN_REQUESTED if len(data) < setup.w_length else ReplyLength.AS_REQUESTED
            self.descriptors.sample(int(_descriptor_kind(setup.descriptor_type)), int(reply),
                                    max(transfer.data_packets, 1), zlp, state)
        elif setup.request is Request.SET_ADDRESS and transfer.succeeded:
            self.addresses.sample(setup.w_value, transfer.naks)
        elif setup.request in (Request.SET_FEATURE, Request.CLEAR_FEATURE):
            endpoint_type = self.tracker.endpoint_type(setup.w_index) if self.tracker else None
            self.features.sample(min(setup.w_value, 255), int(setup.recipient),
                                 int(setup.request is Request.SET_FEATURE), stalled,
                                 int(endpoint_type) if endpoint_type is not None else 0)

    def sample_line_event(self, event: LineEvent) -> None:
        match event.condition, event.source:
            case LineCondition.RESET, _:
                self.bus.sample(int(BusEvent.BUS_RESET))
            case LineCondition.IDLE, _:
                self.bus.sample(int(BusEvent.SUSPEND))
            case LineCondition.RESUME, Source.HOST:
                self.bus.sample(int(BusEvent.HOST_RESUME))
            case LineCondition.RESUME, Source.DEVICE:
                self.bus.sample(int(BusEvent.REMOTE_WAKEUP))

    def report_phase(self):
        report = vsc.get_coverage_report(details=False)
        self.logger.info("functional coverage:\n%s", report)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        name = ConfigDB().get(self, "", "test_name")
        vsc.write_coverage_db(str(self.output_dir / f"coverage_{name}.xml"))
