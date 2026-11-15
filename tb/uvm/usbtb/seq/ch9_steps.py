"""Test steps that several USBCV procedures share.

TD 9.17 runs all of TD 9.2; TD 9.9 is TD 9.4 plus the halt checks; TD 9.3 runs TD 9.8 on
each iFunction. Each function here is one such block of steps, run by a
:class:`~usbtb.seq.ch9_base.Ch9Sequence` in its current state.
"""
from __future__ import annotations

from ..ch9 import usbcv
from ..ch9.constants import (
    DescriptorType,
    Direction,
    EndpointStatus,
    FeatureSelector,
    Recipient,
    TransferType,
)
from ..ch9.descriptors import DescriptorError, DeviceDescriptor, EndpointDescriptor
from ..usb.wire import Pid
from .ch9_base import Ch9Sequence

STRING_REQUEST_LENGTH = 500


async def configuration_descriptor_steps(seq: Ch9Sequence, index: int, other_speed: bool = False) -> None:
    """TD 9.2 steps 2 to 10 for configuration ``index``."""
    host = seq.host
    descriptor_type = DescriptorType.OTHER_SPEED_CONFIGURATION if other_speed else DescriptorType.CONFIGURATION
    name = f"{descriptor_type.name} {index}"
    header = seq.require(await host.get_descriptor(descriptor_type, index, 9), f"GET_DESCRIPTOR({name}, 9)")
    if not seq.check_findings(usbcv.check_configuration_header(header.data, other_speed), f"{name} (step 3)"):
        return
    total = int.from_bytes(header.data[2:4], "little")
    full = seq.require(await host.get_descriptor(descriptor_type, index, total), f"GET_DESCRIPTOR({name}, {total})")
    seq.check_findings(usbcv.check_configuration_set(full.data, total), f"{name} (steps 5-7)")
    again = seq.require(await host.get_descriptor(descriptor_type, index, 9), f"GET_DESCRIPTOR({name}, 9)")
    seq.check_findings(usbcv.check_configuration_attributes(again.data), f"{name} (step 9)")
    if not other_speed:
        result, status = await host.get_status(Recipient.DEVICE)
        seq.require(result, "GET_STATUS(device)")
        seq.check_findings(usbcv.check_configuration_power(again.data, status), f"{name} (step 10)")


async def interface_steps(seq: Ch9Sequence, index: int, halt_endpoints: bool = False) -> None:
    """TD 9.4 steps 2 to 10 (TD 9.9 steps 2 to 11 with ``halt_endpoints``)."""
    data = await seq.read_configuration(index)
    if data is None:
        return
    try:
        parsed = usbcv.parse_configuration(data)
    except DescriptorError as error:
        seq.check(False, f"configuration {index} is malformed: {error}")
        return
    value = parsed.header.configuration_value
    await seq.set_and_verify_configuration(value)  # step 5
    seq.check_findings(usbcv.check_interfaces(data), f"configuration {index} interfaces (steps 6-9)")
    for group in parsed.groups:  # step 10
        interface = group.interface
        number, alternate = interface.interface_number, interface.alternate_setting
        has_alternates = sum(1 for i in parsed.interfaces if i.interface_number == number) > 1
        result = await seq.host.set_interface(number, alternate)
        if result.stalled and not has_alternates:
            seq.info(f"SET_INTERFACE({number}, 0) stalled: allowed, the interface has no alternate settings")
            continue
        seq.require(result, f"SET_INTERFACE({number}, {alternate})")
        result, reported = await seq.host.get_interface(number)
        seq.require(result, f"GET_INTERFACE({number})")
        seq.check(reported == alternate, f"GET_INTERFACE({number}) returned {reported}, expected {alternate}")
        if halt_endpoints:
            for endpoint in group.endpoints:
                if endpoint.transfer_type in (TransferType.BULK, TransferType.INTERRUPT):
                    await halt_endpoint_steps(seq, endpoint)


async def halt_endpoint_steps(seq: Ch9Sequence, endpoint: EndpointDescriptor) -> None:
    """TD 9.9 step 11 for one bulk or interrupt endpoint.

    Beyond USBCV, which only reads the status back, the endpoint itself is probed with a
    token: a halted endpoint must answer STALL, and must stop doing so once cleared.
    """
    host, address = seq.host, endpoint.endpoint_address
    name = f"endpoint 0x{address:02X}"

    async def halted() -> bool | None:
        result, status = await host.get_status(Recipient.ENDPOINT, address)
        seq.require(result, f"GET_STATUS({name})")
        return bool(status & EndpointStatus.HALT)

    if await halted():
        seq.require(await host.clear_feature(FeatureSelector.ENDPOINT_HALT, Recipient.ENDPOINT, address),
                    f"CLEAR_FEATURE(ENDPOINT_HALT, {name})")
        seq.check(not await halted(), f"{name} still reports halt after CLEAR_FEATURE")
    seq.require(await host.set_feature(FeatureSelector.ENDPOINT_HALT, Recipient.ENDPOINT, address),
                f"SET_FEATURE(ENDPOINT_HALT, {name})")
    seq.check(await halted(), f"{name} does not report halt after SET_FEATURE(ENDPOINT_HALT)")
    answer = await probe_endpoint(seq, endpoint)
    seq.check(answer is Pid.STALL, f"halted {name} answered {answer.name if answer else 'nothing'}, expected STALL")
    seq.require(await host.clear_feature(FeatureSelector.ENDPOINT_HALT, Recipient.ENDPOINT, address),
                f"CLEAR_FEATURE(ENDPOINT_HALT, {name})")
    seq.check(not await halted(), f"{name} still reports halt after CLEAR_FEATURE(ENDPOINT_HALT)")
    answer = await probe_endpoint(seq, endpoint)
    seq.check(answer not in (Pid.STALL, None), f"{name} answered {answer.name if answer else 'nothing'} "
                                               "after the halt was cleared")


async def probe_endpoint(seq: Ch9Sequence, endpoint: EndpointDescriptor) -> Pid | None:
    """One IN token, or one zero-length OUT, to a non-control endpoint; returns its answer."""
    if endpoint.direction is Direction.IN:
        txn = await seq.host.transaction(Pid.IN, endpoint.number)
    else:
        txn = await seq.host.transaction(Pid.OUT, endpoint.number, b"", Pid.DATA0)
    return txn.response


async def endpoint_steps(seq: Ch9Sequence, index: int) -> None:
    """TD 9.5 steps 2 to 13."""
    data = await seq.read_configuration(index)
    if data is None:
        return
    value = int(data[5])
    await seq.set_and_verify_configuration(value)
    device = seq.require(await seq.host.get_device_descriptor(18), "GET_DESCRIPTOR(DEVICE, 18)")
    bcd_usb = DeviceDescriptor.from_bytes(device.data).bcd_usb
    seq.check_findings(usbcv.check_endpoints(data, bcd_usb), f"configuration {index} endpoints (steps 7-13)")


async def string_steps(seq: Ch9Sequence, indices: list[int]) -> None:
    """TD 9.8 steps 3 to 6 for the given string indices."""
    if not indices:
        seq.info("no string descriptors referenced")
        return
    host = seq.host
    table = seq.require(await host.get_descriptor(DescriptorType.STRING, 0, STRING_REQUEST_LENGTH),
                        "GET_DESCRIPTOR(STRING 0, 500)")
    if not seq.check_findings(usbcv.check_language_table(table.data), "LANGID table (step 4)"):
        return
    languages = [int.from_bytes(table.data[i:i + 2], "little") for i in range(2, len(table.data) - 1, 2)]
    for language in languages:
        for index in indices:
            result = seq.require(await host.get_descriptor(DescriptorType.STRING, index, STRING_REQUEST_LENGTH,
                                                           language),
                                 f"GET_DESCRIPTOR(STRING {index}, LANGID 0x{language:04X}, 500)")
            seq.check_findings(usbcv.check_string(result.data, index, language), "step 6")


async def all_string_indices(seq: Ch9Sequence) -> list[int]:
    """Every string index named by the device, configuration, interface and IAD descriptors."""
    device = DeviceDescriptor.from_bytes(seq.enumeration.device_bytes)
    configurations = []
    for index in range(len(seq.enumeration.configurations)):
        data = await seq.read_configuration(index)
        if data:
            configurations.append(data)
    return usbcv.string_indices(device, configurations)
