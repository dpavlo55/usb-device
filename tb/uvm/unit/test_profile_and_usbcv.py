"""Random profiles are legal (every USBCV check passes) and every check catches its violation."""
import dataclasses

import pytest

from usbtb.ch9 import usbcv
from usbtb.ch9.constants import DescriptorType, TransferType
from usbtb.ch9.descriptors import (
    Configuration,
    DeviceDescriptor,
    EndpointDescriptor,
    Interface,
    InterfaceAssociationDescriptor,
    InterfaceDescriptor,
    StringDescriptor,
)
from usbtb.ch9.profile import DeviceProfile, HardwareLimits

SEEDS = range(40)


@pytest.fixture(scope="module")
def profiles():
    return {seed: DeviceProfile(seed).descriptor_set() for seed in SEEDS}


@pytest.mark.parametrize("seed", SEEDS)
def test_random_profiles_pass_every_check(profiles, seed):
    descriptors = profiles[seed]
    device = descriptors.device
    assert usbcv.check_device_descriptor(device.to_bytes()) == []
    for configuration in descriptors.configurations:
        data = configuration.to_bytes()
        assert usbcv.check_configuration_header(data[:9]) == []
        assert usbcv.check_configuration_set(data, len(data)) == []
        assert usbcv.check_configuration_attributes(data[:9]) == []
        status = 1 if configuration.header().self_powered else 0
        assert usbcv.check_configuration_power(data[:9], status) == []
        assert usbcv.check_interface_associations(data) == []
        assert usbcv.check_interfaces(data) == []
        assert usbcv.check_endpoints(data, device.bcd_usb) == []
    assert usbcv.check_language_table(descriptors.string_bytes(0, 0)) == []
    for index in descriptors.strings:
        for language in descriptors.language_ids:
            assert usbcv.check_string(descriptors.string_bytes(index, language), index, language) == []


@pytest.mark.parametrize("seed", SEEDS)
def test_profiles_fit_the_hardware(profiles, seed):
    limits = HardwareLimits()
    for configuration in profiles[seed].configurations:
        endpoints = configuration.endpoints
        assert len(endpoints) <= limits.endpoint_entries
        assert sum(limits.buffer_size(ep.max_packet_size) for ep in endpoints) <= limits.buffer_bytes
        addresses = [ep.endpoint_address for ep in endpoints]
        assert len(addresses) == len(set(addresses))


@pytest.mark.parametrize("seed", SEEDS)
def test_all_configurations_share_the_power_source(profiles, seed):
    """GET_STATUS reports one physical power source, so TD 9.2 step 10 checks every
    configuration against it."""
    assert len({c.header().self_powered for c in profiles[seed].configurations}) == 1


def test_profiles_are_reproducible_and_varied(profiles):
    assert DeviceProfile(7).descriptor_set() == profiles[7]
    devices = [p.device for p in profiles.values()]
    assert {d.max_packet_size0 for d in devices} == {8, 16, 32, 64}
    assert {len(p.configurations) for p in profiles.values()} == {1, 2, 3}
    assert any(c.associations for p in profiles.values() for c in p.configurations)
    kinds = {ep.transfer_type for p in profiles.values() for c in p.configurations for ep in c.endpoints}
    assert kinds == {TransferType.BULK, TransferType.INTERRUPT, TransferType.ISOCHRONOUS}


# Negative cases: one deliberate violation each -------------------------------------------------

GOOD_DEVICE = DeviceDescriptor(0x0200, 0, 0, 0, 64, 0x1234, 0x0001, 0x0100, 0, 0, 0, 1)


@pytest.mark.parametrize("change, expected", [
    ({"length": 17}, "bLength is 17"),
    ({"type_code": 2}, "bDescriptorType"),
    ({"bcd_usb": 0x0300}, "hibyte"),
    ({"bcd_usb": 0x0120}, "low byte"),
    ({"device_subclass": 1}, "bDeviceSubClass"),
    ({"max_packet_size0": 12}, "bMaxPacketSize0"),
    ({"num_configurations": 0}, "bNumConfigurations"),
    ({"device_class": 0x03}, "class code"),
])
def test_device_descriptor_violations(change, expected):
    findings = usbcv.check_device_descriptor(dataclasses.replace(GOOD_DEVICE, **change).to_bytes())
    assert any(expected in f for f in findings), findings


def _configuration(endpoints=(), interfaces=None, attributes=0x80, max_power=50, associations=()):
    interfaces = interfaces or [Interface(InterfaceDescriptor(0, 0, len(endpoints), 0xFF, 0, 0, 0),
                                          list(endpoints))]
    return Configuration(1, attributes, max_power, interfaces=interfaces, associations=list(associations))


@pytest.mark.parametrize("endpoint, bcd_usb, expected", [
    (EndpointDescriptor(0x81, TransferType.BULK, 12, 0), 0x0200, "bulk wMaxPacketSize"),
    (EndpointDescriptor(0x81, TransferType.INTERRUPT, 64, 0), 0x0200, "bInterval must not be 0"),
    (EndpointDescriptor(0x81, TransferType.INTERRUPT, 65, 1), 0x0200, "below 65"),
    (EndpointDescriptor(0x80, TransferType.BULK, 64, 0), 0x0200, "endpoint zero"),
    (EndpointDescriptor(0x81, TransferType.BULK | 0x40, 64, 0), 0x0200, "D7..D6"),
    (EndpointDescriptor(0x81, TransferType.BULK | 0x04, 64, 0), 0x0200, "D5..D2"),
    (EndpointDescriptor(0x81, TransferType.ISOCHRONOUS | 0x04, 64, 1), 0x0110, "D5..D2"),
    (EndpointDescriptor(0x81, TransferType.ISOCHRONOUS, 64, 2), 0x0110, "must be 1"),
    (EndpointDescriptor(0x81, TransferType.ISOCHRONOUS, 64, 17), 0x0200, "1..16"),
    (EndpointDescriptor(0x81, TransferType.BULK, 64 | 0x0800, 0), 0x0200, "D12..D11"),
    (EndpointDescriptor(0x81, TransferType.BULK, 64 | 0x2000, 0), 0x0200, "D15..D13"),
])
def test_endpoint_violations(endpoint, bcd_usb, expected):
    findings = usbcv.check_endpoints(_configuration([endpoint]).to_bytes(), bcd_usb)
    assert any(expected in f for f in findings), findings


def test_short_endpoint_descriptor_is_reported():
    interface = InterfaceDescriptor(0, 0, 1, 0xFF, 0, 0, 0).to_bytes()
    short_endpoint = bytes((6, DescriptorType.ENDPOINT, 0x81, TransferType.BULK, 64, 0))
    body = interface + short_endpoint
    header = _configuration().header(total_length=9 + len(body)).to_bytes()
    findings = usbcv.check_endpoints(header + body, 0x0200)
    assert any("bLength 6" in f for f in findings), findings


def test_iso_in_default_interface_violates_bandwidth_rule():
    data = _configuration([EndpointDescriptor(0x81, TransferType.ISOCHRONOUS, 64, 1)]).to_bytes()
    assert any("isochronous" in f for f in usbcv.check_interfaces(data))


@pytest.mark.parametrize("interfaces, expected", [
    ([Interface(InterfaceDescriptor(1, 0, 0, 0xFF, 0, 0, 0))], "expected 0 alt 0"),
    ([Interface(InterfaceDescriptor(0, 0, 0, 0xFF, 0, 0, 0)),
      Interface(InterfaceDescriptor(0, 2, 0, 0xFF, 0, 0, 0))], "alternate setting does not follow"),
    ([Interface(InterfaceDescriptor(0, 0, 0, 0xFF, 0, 0, 0)),
      Interface(InterfaceDescriptor(2, 0, 0, 0xFF, 0, 0, 0))], "does not follow"),
    ([Interface(InterfaceDescriptor(0, 0, 0, 0x00, 1, 0, 0))], "bInterfaceClass 0"),
    ([Interface(InterfaceDescriptor(0, 0, 2, 0xFF, 0, 0, 0),
                [EndpointDescriptor(0x81, TransferType.BULK, 64, 0)])], "bNumEndpoints"),
])
def test_interface_violations(interfaces, expected):
    findings = usbcv.check_interfaces(_configuration(interfaces=interfaces).to_bytes())
    assert any(expected in f for f in findings), findings


def test_configuration_violations():
    bad_count = _configuration(interfaces=[Interface(InterfaceDescriptor(0, 0, 1, 0xFF, 0, 0, 0))])
    data = bad_count.to_bytes()
    assert any("endpoint descriptor" in f for f in usbcv.check_configuration_set(data, len(data)))
    assert usbcv.check_configuration_set(data, len(data) + 1)
    assert any("D4..D0" in f for f in usbcv.check_configuration_attributes(
        _configuration(attributes=0x81).to_bytes()[:9]))
    assert any("D7" in f for f in usbcv.check_configuration_attributes(
        _configuration(attributes=0x40).to_bytes()[:9]))
    assert any("self powered" in f for f in usbcv.check_configuration_attributes(
        _configuration(max_power=0).to_bytes()[:9]))
    assert usbcv.check_configuration_power(_configuration(max_power=51).to_bytes()[:9], device_status=1)
    assert usbcv.check_configuration_power(_configuration(max_power=251).to_bytes()[:9], device_status=0)


def test_iad_violations():
    interfaces = [Interface(InterfaceDescriptor(0, 0, 0, 0x02, 0, 0, 0)),
                  Interface(InterfaceDescriptor(1, 0, 0, 0x0A, 0, 0, 0))]
    good = InterfaceAssociationDescriptor(0, 2, 0x02, 0, 0, 0)
    assert usbcv.check_interface_associations(
        _configuration(interfaces=interfaces, associations=[good]).to_bytes()) == []
    for bad, expected in ((dataclasses.replace(good, function_class=0), "bFunctionClass"),
                          (dataclasses.replace(good, interface_count=3), "bInterfaceCount"),
                          (dataclasses.replace(good, length=9), "bLength")):
        data = _configuration(interfaces=interfaces, associations=[bad]).to_bytes()
        findings = usbcv.check_interface_associations(data)
        assert any(expected in f for f in findings), findings


def test_string_violations():
    assert usbcv.check_language_table(bytes((2, 3)))
    assert usbcv.check_language_table(bytes((5, 3, 9, 4, 0)))
    assert usbcv.check_language_table(bytes((4, 2, 9, 4)))
    assert usbcv.check_string(bytes((6, 3, 65, 0)), 1, 0x409)
    assert usbcv.check_string(StringDescriptor.from_text("ok").raw, 1, 0x409) == []


def test_device_qualifier_and_bos():
    assert usbcv.check_device_qualifier(bytes((10, 6, 0, 2, 0, 0, 0, 64, 1, 0))) == []
    assert any("bReserved" in f for f in usbcv.check_device_qualifier(bytes((10, 6, 0, 2, 0, 0, 0, 64, 1, 1))))
    bos = bytes((5, DescriptorType.BOS, 12, 0, 1)) + bytes((7, 16, 2, 2, 0, 0, 0))
    assert usbcv.check_bos(bos, len(bos)) == []
    assert any("LPM" in f for f in usbcv.check_bos(bos[:-4] + bytes((0, 0, 0, 0)), len(bos)))
    assert any("bNumDeviceCaps" in f for f in usbcv.check_bos(bos[:4] + b"\x02" + bos[5:], len(bos)))


def test_configuration_summaries():
    one = _configuration(interfaces=[Interface(InterfaceDescriptor(0, 0, 0, 0x02, 0, 0, 0))]).to_bytes()
    two = _configuration(interfaces=[Interface(InterfaceDescriptor(0, 0, 0, 0x08, 6, 0x50, 0))]).to_bytes()
    summary = bytes((10, 16, 0x10, 0x00, 0x01, 0x08, 6, 0x50, 1, 1))
    other = bytes((10, 16, 0x10, 0x00, 0x01, 0x02, 0, 0, 1, 0))
    assert usbcv.check_configuration_summaries([summary, other], [one, two]) == []
    assert any("has no Configuration Summary" in f for f in usbcv.check_configuration_summaries([summary], [one, two]))
    assert any("single-configuration" in f for f in usbcv.check_configuration_summaries([summary], [two]))
