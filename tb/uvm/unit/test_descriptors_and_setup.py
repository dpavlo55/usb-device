"""SETUP packets and descriptors survive a round trip through bytes."""
import pytest

from usbtb.ch9.constants import DescriptorType, Direction, FeatureSelector, Recipient, Request, TransferType
from usbtb.ch9.descriptors import (
    Configuration,
    DescriptorError,
    DeviceDescriptor,
    EndpointDescriptor,
    Interface,
    InterfaceAssociationDescriptor,
    InterfaceDescriptor,
    StringDescriptor,
    iter_descriptors,
)
from usbtb.ch9.setup import SetupPacket


def test_setup_packet_round_trip():
    setup = SetupPacket.get_descriptor(DescriptorType.STRING, 2, 500, language_id=0x0409)
    assert setup.to_bytes() == bytes((0x80, 6, 2, 3, 0x09, 0x04, 0xF4, 0x01))
    assert SetupPacket.from_bytes(setup.to_bytes()) == setup
    assert setup.direction is Direction.IN
    assert setup.request is Request.GET_DESCRIPTOR
    assert (setup.descriptor_type, setup.descriptor_index) == (DescriptorType.STRING, 2)


def test_feature_requests_encode_recipient():
    setup = SetupPacket.set_feature(FeatureSelector.ENDPOINT_HALT, Recipient.ENDPOINT, 0x81)
    assert setup.bm_request_type == 0x02
    assert setup.recipient is Recipient.ENDPOINT
    assert (setup.w_value, setup.w_index, setup.w_length) == (0, 0x81, 0)


def test_setup_rejects_bad_fields():
    with pytest.raises(ValueError):
        SetupPacket(0x80, 6, 0x10000)
    with pytest.raises(ValueError):
        SetupPacket.from_bytes(b"\x00" * 7)


def test_configuration_hierarchy_round_trip():
    configuration = Configuration(1, 0x80, 50, interfaces=[
        Interface(InterfaceDescriptor(0, 0, 1, 0xFF, 0, 0, 0),
                  [EndpointDescriptor(0x81, TransferType.BULK, 64, 0)]),
        Interface(InterfaceDescriptor(1, 0, 0, 0x02, 0, 0, 0)),
    ], associations=[InterfaceAssociationDescriptor(0, 2, 0xFF, 0, 0, 0)])
    data = configuration.to_bytes()
    header = configuration.header()
    assert header.total_length == len(data) == 9 + 8 + 9 + 7 + 9
    types = [d.type_code for d in iter_descriptors(data)]
    assert types == [DescriptorType.CONFIGURATION, DescriptorType.INTERFACE_ASSOCIATION,
                     DescriptorType.INTERFACE, DescriptorType.ENDPOINT, DescriptorType.INTERFACE]


def test_device_descriptor_round_trip():
    descriptor = DeviceDescriptor(0x0200, 0, 0, 0, 64, 0x1234, 0x5678, 0x0100, 1, 2, 0, 1)
    assert DeviceDescriptor.from_bytes(descriptor.to_bytes()) == descriptor


def test_strings():
    table = StringDescriptor.language_table((0x0409, 0x0407))
    assert table.language_ids == (0x0409, 0x0407) and table.length == 6
    assert StringDescriptor.from_text("Hi").raw == bytes((6, 3)) + "Hi".encode("utf-16-le")


def test_truncated_descriptor_sets_are_errors():
    with pytest.raises(DescriptorError):
        list(iter_descriptors(bytes((9, 2, 18, 0, 1, 1, 0, 0x80, 50, 9, 4))))
    with pytest.raises(DescriptorError):
        list(iter_descriptors(bytes((0, 2))))
