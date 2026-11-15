"""The descriptor content checks of the USBCV Chapter 9 tests, as pure functions.

Each function takes the bytes a device returned and gives back a list of failure messages
(empty when the data passes). The step numbers in the docstrings refer to the USB-IF
"USB 2.0 Command Verifier Compliance Test Specification" (TD 9.x) and, for TD 9.3 and
TD 9.30, to the "USB 3.2 CV Compliance Test Specification" v1.22.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from .constants import (
    DescriptorType,
    DeviceCapabilityType,
    DEVICE_CLASS_CODES,
    FULL_SPEED_EP0_SIZES,
    Speed,
    TransferType,
)
from .descriptors import (
    BosDescriptor,
    ConfigurationDescriptor,
    Descriptor,
    DescriptorError,
    DeviceCapabilityDescriptor,
    DeviceDescriptor,
    DeviceQualifierDescriptor,
    EndpointDescriptor,
    InterfaceAssociationDescriptor,
    InterfaceDescriptor,
    RawDescriptor,
    StringDescriptor,
    iter_descriptors,
)

Findings = list[str]

AUDIO_CLASS = 0x01
_ALLOWED_IN_CONFIGURATION = {DescriptorType.INTERFACE, DescriptorType.ENDPOINT,
                             DescriptorType.INTERFACE_ASSOCIATION, DescriptorType.OTG,
                             DescriptorType.INTERFACE_POWER}


def _is_class_specific(type_code: int) -> bool:
    return type_code >= 0x20


# Parsed views ------------------------------------------------------------------------------

@dataclass
class InterfaceGroup:
    """An interface descriptor and the descriptors that follow it up to the next interface."""

    interface: InterfaceDescriptor
    endpoints: list[EndpointDescriptor] = field(default_factory=list)
    others: list[Descriptor] = field(default_factory=list)


@dataclass
class ParsedConfiguration:
    header: ConfigurationDescriptor
    descriptors: list[Descriptor]
    groups: list[InterfaceGroup]
    associations: list[tuple[int, InterfaceAssociationDescriptor]]

    @property
    def interfaces(self) -> list[InterfaceDescriptor]:
        return [g.interface for g in self.groups]

    @property
    def endpoints(self) -> list[tuple[InterfaceDescriptor, EndpointDescriptor]]:
        return [(g.interface, ep) for g in self.groups for ep in g.endpoints]

    @property
    def string_indices(self) -> set[int]:
        indices = {self.header.i_configuration}
        indices |= {g.interface.i_interface for g in self.groups}
        indices |= {iad.i_function for _, iad in self.associations}
        return indices - {0}


def parse_configuration(data: bytes) -> ParsedConfiguration:
    """Split a full configuration descriptor set; raises :class:`DescriptorError` when malformed."""
    descriptors = list(iter_descriptors(data))
    if not descriptors or not isinstance(descriptors[0], ConfigurationDescriptor):
        raise DescriptorError("the first descriptor is not a configuration descriptor")
    groups: list[InterfaceGroup] = []
    associations = []
    for position, descriptor in enumerate(descriptors[1:], start=1):
        match descriptor:
            case InterfaceDescriptor():
                groups.append(InterfaceGroup(descriptor))
            case InterfaceAssociationDescriptor():
                associations.append((position, descriptor))
            case EndpointDescriptor() if groups:
                groups[-1].endpoints.append(descriptor)
            case _ if groups:
                groups[-1].others.append(descriptor)
    return ParsedConfiguration(descriptors[0], descriptors, groups, associations)


# TD 9.1 Device Descriptor Test -------------------------------------------------------------

def check_device_descriptor(data: bytes, speed: Speed = Speed.FULL,
                            vendor_ids: frozenset[int] | None = None) -> Findings:
    """TD 9.1 step 3. ``vendor_ids`` is the USB-IF vendor list; ``None`` skips that check."""
    if len(data) != DeviceDescriptor.SIZE:
        return [f"GET_DESCRIPTOR(DEVICE, 18) returned {len(data)} bytes, expected 18"]
    d = DeviceDescriptor.from_bytes(data)
    findings: Findings = []
    if d.length != 18:
        findings.append(f"bLength is {d.length}, expected 18")
    if d.type_code != DescriptorType.DEVICE:
        findings.append(f"bDescriptorType is {d.type_code}, expected DEVICE (1)")
    major, minor = d.bcd_usb >> 8, d.bcd_usb & 0xFF
    if major not in (0x01, 0x02):
        findings.append(f"bcdUSB.hibyte is 0x{major:02X}, expected 01 or 02")
    if speed is Speed.HIGH and major != 0x02:
        findings.append("a high-speed device must report bcdUSB.hibyte 02")
    if major == 0x01 and minor not in (0x00, 0x01, 0x10):
        findings.append(f"bcdUSB is 0x{d.bcd_usb:04X}: for 1.x the low byte must be 00, 01 or 10")
    if d.device_class == 0 and d.device_subclass != 0:
        findings.append(f"bDeviceClass is 0 but bDeviceSubClass is 0x{d.device_subclass:02X}")
    expected_mps0 = {Speed.LOW: (8,), Speed.FULL: FULL_SPEED_EP0_SIZES, Speed.HIGH: (64,)}[speed]
    if d.max_packet_size0 not in expected_mps0:
        findings.append(f"bMaxPacketSize0 is {d.max_packet_size0}, expected one of {expected_mps0}")
    if vendor_ids is not None and d.id_vendor not in vendor_ids:
        findings.append(f"idVendor 0x{d.id_vendor:04X} is not in the USB-IF vendor list")
    if d.num_configurations == 0:
        findings.append("bNumConfigurations is 0")
    if d.device_class not in DEVICE_CLASS_CODES:
        findings.append(f"bDeviceClass 0x{d.device_class:02X} is not a class code assigned for devices")
    return findings


# TD 9.2 Configuration Descriptor Test ------------------------------------------------------

def check_configuration_header(data: bytes, other_speed: bool = False) -> Findings:
    """TD 9.2 step 3 (also the first check of TD 9.3, 9.4, 9.5 and 9.9)."""
    if len(data) != ConfigurationDescriptor.SIZE:
        return [f"GET_DESCRIPTOR(CONFIGURATION, 9) returned {len(data)} bytes, expected 9"]
    header = ConfigurationDescriptor.from_bytes(data)
    findings: Findings = []
    if header.length != 9:
        findings.append(f"bLength is {header.length}, expected 9")
    expected = DescriptorType.OTHER_SPEED_CONFIGURATION if other_speed else DescriptorType.CONFIGURATION
    if header.type_code not in (DescriptorType.CONFIGURATION, DescriptorType.OTHER_SPEED_CONFIGURATION):
        findings.append(f"bDescriptorType is {header.type_code}, expected {expected.name} ({expected:d})")
    return findings


def check_configuration_set(data: bytes, total_length: int) -> Findings:
    """TD 9.2 steps 5 to 7: the hierarchy returned for wLength = wTotalLength."""
    if len(data) != total_length:
        return [f"wTotalLength is {total_length} but {len(data)} bytes were returned"]
    try:
        parsed = parse_configuration(data)
    except DescriptorError as error:
        return [f"configuration descriptor set is malformed: {error}"]
    findings = check_configuration_header(data[:9])
    for descriptor in parsed.descriptors[1:]:
        type_code = getattr(descriptor, "type_code", descriptor.descriptor_type)
        if type_code in (DescriptorType.CONFIGURATION, DescriptorType.OTHER_SPEED_CONFIGURATION):
            findings.append("the set holds more than one configuration descriptor")
        elif type_code not in _ALLOWED_IN_CONFIGURATION and not _is_class_specific(type_code):
            findings.append(f"descriptor type {type_code} is not an interface, endpoint or class descriptor")
    default_interfaces = sum(1 for i in parsed.interfaces if i.alternate_setting == 0)
    if default_interfaces != parsed.header.num_interfaces:
        findings.append(f"found {default_interfaces} interface(s) with bAlternateSetting 0, "
                        f"bNumInterfaces is {parsed.header.num_interfaces}")
    declared = sum(i.num_endpoints for i in parsed.interfaces)
    found = len(parsed.endpoints)
    if declared != found:
        findings.append(f"found {found} endpoint descriptor(s), the interfaces declare {declared}")
    return findings


def check_configuration_attributes(data: bytes) -> Findings:
    """TD 9.2 step 9."""
    header = ConfigurationDescriptor.from_bytes(data)
    findings = check_configuration_header(data)
    if header.num_interfaces == 0:
        findings.append("bNumInterfaces is 0")
    if header.attributes & 0x1F:
        findings.append(f"bmAttributes 0x{header.attributes:02X}: bits D4..D0 must be 0")
    if not header.attributes & 0x80:
        findings.append(f"bmAttributes 0x{header.attributes:02X}: bit D7 must be 1")
    if header.max_power == 0 and not header.self_powered:
        findings.append("bMaxPower is 0 but bmAttributes does not report self powered (D6)")
    return findings


def check_configuration_power(data: bytes, device_status: int) -> Findings:
    """TD 9.2 step 10: bMaxPower against the power source reported by GET_STATUS(device)."""
    header = ConfigurationDescriptor.from_bytes(data)
    self_powered = bool(device_status & 0x1)
    limit = 100 if self_powered else 500
    if header.max_power_ma > limit:
        source = "self" if self_powered else "bus"
        return [f"{source} powered device declares bMaxPower {header.max_power_ma} mA, limit {limit} mA"]
    return []


# TD 9.3 Interface Association Descriptor Test (USB 3.2 CV spec) ----------------------------

def check_interface_associations(data: bytes) -> Findings:
    """TD 9.3 step 5 for every IAD in a configuration set."""
    try:
        parsed = parse_configuration(data)
    except DescriptorError as error:
        return [f"configuration descriptor set is malformed: {error}"]
    findings: Findings = []
    interfaces = sorted({i.interface_number for i in parsed.interfaces})
    for position, iad in parsed.associations:
        name = f"IAD for interface {iad.first_interface}"
        if iad.length != 8:
            findings.append(f"{name}: bLength is {iad.length}, expected 8")
        if iad.type_code != DescriptorType.INTERFACE_ASSOCIATION:
            findings.append(f"{name}: bDescriptorType is {iad.type_code}, expected 11")
        if iad.function_class == 0:
            findings.append(f"{name}: bFunctionClass is 0")
        wanted = set(iad.interfaces)
        if iad.interface_count == 0 or not wanted <= set(interfaces):
            findings.append(f"{name}: bInterfaceCount {iad.interface_count} does not name existing "
                            f"contiguous interfaces (interfaces present: {interfaces})")
        following = parsed.descriptors[position + 1] if position + 1 < len(parsed.descriptors) else None
        if not (isinstance(following, InterfaceDescriptor) and following.interface_number == iad.first_interface):
            findings.append(f"{name}: is not followed by interface {iad.first_interface}")
    return findings


# TD 9.4 Interface Descriptor Test ----------------------------------------------------------

def check_interface_bandwidth(parsed: ParsedConfiguration) -> Findings:
    """TD 9.4 / 9.9 step 6: default interfaces must not reserve periodic bandwidth."""
    findings: Findings = []
    for interface, endpoint in parsed.endpoints:
        if interface.alternate_setting != 0:
            continue
        where = f"interface {interface.interface_number} alt 0 endpoint 0x{endpoint.endpoint_address:02X}"
        if endpoint.transfer_type is TransferType.ISOCHRONOUS and endpoint.packet_size > 0:
            findings.append(f"{where}: isochronous with wMaxPacketSize {endpoint.packet_size} > 0")
        if endpoint.transfer_type is TransferType.INTERRUPT and endpoint.packet_size > 64:
            findings.append(f"{where}: interrupt with wMaxPacketSize {endpoint.packet_size} > 64")
    return findings


def check_interfaces(data: bytes) -> Findings:
    """TD 9.4 steps 6 to 9."""
    try:
        parsed = parse_configuration(data)
    except DescriptorError as error:
        return [f"configuration descriptor set is malformed: {error}"]
    findings = check_interface_bandwidth(parsed)
    if not parsed.groups:
        return findings + ["no interface descriptor was returned"]
    first = parsed.groups[0].interface
    if (first.interface_number, first.alternate_setting) != (0, 0):
        findings.append(f"the first interface is {first.interface_number} alt {first.alternate_setting}, "
                        "expected 0 alt 0")
    previous = None
    for group in parsed.groups:
        i = group.interface
        name = f"interface {i.interface_number} alt {i.alternate_setting}"
        if previous is not None:
            if i.interface_number == previous.interface_number:
                if i.alternate_setting != previous.alternate_setting + 1:
                    findings.append(f"{name}: alternate setting does not follow {previous.alternate_setting}")
            else:
                if i.interface_number != previous.interface_number + 1:
                    findings.append(f"{name}: interface number does not follow {previous.interface_number}")
                if i.alternate_setting != 0:
                    findings.append(f"{name}: first alternate setting of an interface must be 0")
                if i.interface_number >= parsed.header.num_interfaces:
                    findings.append(f"{name}: interface number is not below bNumInterfaces "
                                    f"({parsed.header.num_interfaces})")
        if i.length <= 8:
            findings.append(f"{name}: bLength {i.length} must be greater than 8")
        if i.type_code != DescriptorType.INTERFACE:
            findings.append(f"{name}: bDescriptorType is {i.type_code}, expected 4")
        if i.interface_class == 0 and i.interface_subclass != 0:
            findings.append(f"{name}: bInterfaceClass 0 with bInterfaceSubClass 0x{i.interface_subclass:02X}")
        if i.num_endpoints != len(group.endpoints):
            findings.append(f"{name}: bNumEndpoints is {i.num_endpoints}, "
                            f"{len(group.endpoints)} endpoint descriptor(s) follow")
        previous = i
    return findings


# TD 9.5 Endpoint Descriptor Test -----------------------------------------------------------

def check_endpoints(data: bytes, bcd_usb: int) -> Findings:
    """TD 9.5 steps 7 to 13 for a full-speed device reading its own-speed configuration."""
    try:
        parsed = parse_configuration(data)
    except DescriptorError as error:
        return [f"configuration descriptor set is malformed: {error}"]
    category = "CV_FULL_2X" if bcd_usb >= 0x0200 else "CV_FULL_1X"
    findings: Findings = [f"an endpoint descriptor has bLength {d.length}; it must be greater than 6"
                          for d in parsed.descriptors
                          if isinstance(d, RawDescriptor) and d.descriptor_type == DescriptorType.ENDPOINT]
    for interface, ep in parsed.endpoints:
        name = f"endpoint 0x{ep.endpoint_address:02X}"
        if ep.length <= 6:
            findings.append(f"{name}: bLength {ep.length} must be greater than 6")
        if ep.type_code != DescriptorType.ENDPOINT:
            findings.append(f"{name}: bDescriptorType is {ep.type_code}, expected 5")
        if ep.endpoint_address in (0x00, 0x80):
            findings.append(f"{name}: endpoint zero must not have an endpoint descriptor")
        if ep.attributes & 0xC0:
            findings.append(f"{name}: bmAttributes 0x{ep.attributes:02X}: bits D7..D6 must be 0")
        audio_exception = bcd_usb in (0x0101, 0x0110) and interface.interface_class == AUDIO_CLASS
        iso_2x = ep.transfer_type is TransferType.ISOCHRONOUS and bcd_usb >= 0x0200
        if ep.attributes & 0x3C and not (iso_2x or audio_exception):
            findings.append(f"{name}: bmAttributes 0x{ep.attributes:02X}: bits D5..D2 must be 0")
        if ep.max_packet_size & 0xE000:
            findings.append(f"{name}: wMaxPacketSize 0x{ep.max_packet_size:04X}: bits D15..D13 must be 0")
        if ep.max_packet_size & 0x1800:
            findings.append(f"{name}: wMaxPacketSize bits D12..D11 are set; only high-speed periodic "
                            f"endpoints may use them ({category})")
        size = ep.packet_size
        match ep.transfer_type:
            case TransferType.CONTROL if size not in FULL_SPEED_EP0_SIZES:
                findings.append(f"{name}: control wMaxPacketSize {size} not in {FULL_SPEED_EP0_SIZES}")
            case TransferType.BULK if size not in FULL_SPEED_EP0_SIZES:
                findings.append(f"{name}: bulk wMaxPacketSize {size} not in {FULL_SPEED_EP0_SIZES}")
            case TransferType.INTERRUPT:
                if ep.interval == 0:
                    findings.append(f"{name}: interrupt bInterval must not be 0")
                if size >= 65:
                    findings.append(f"{name}: interrupt wMaxPacketSize {size} must be below 65")
            case TransferType.ISOCHRONOUS:
                if category == "CV_FULL_1X" and ep.interval != 1:
                    findings.append(f"{name}: isochronous bInterval {ep.interval} must be 1 ({category})")
                if category == "CV_FULL_2X" and not 0 < ep.interval < 17:
                    findings.append(f"{name}: isochronous bInterval {ep.interval} must be 1..16 ({category})")
                if size >= 1024:
                    findings.append(f"{name}: isochronous wMaxPacketSize {size} must be below 1024")
    return findings


# TD 9.7 BOS Descriptor Test ----------------------------------------------------------------

def check_bos(data: bytes, total_length: int, superspeed: bool = False) -> Findings:
    """TD 9.7 steps 6 to 11 (devices with bcdUSB above 0x0200)."""
    if len(data) != total_length:
        return [f"BOS wTotalLength is {total_length} but {len(data)} bytes were returned"]
    try:
        descriptors = list(iter_descriptors(data))
    except DescriptorError as error:
        return [f"BOS descriptor set is malformed: {error}"]
    findings: Findings = []
    bos = descriptors[0]
    if not isinstance(bos, BosDescriptor):
        return ["the first descriptor of the BOS set is not a BOS descriptor"]
    if bos.length != 5:
        findings.append(f"BOS bLength is {bos.length}, expected 5")
    capabilities = [d for d in descriptors[1:] if isinstance(d, DeviceCapabilityDescriptor)]
    if bos.num_device_caps != len(capabilities):
        findings.append(f"bNumDeviceCaps is {bos.num_device_caps}, {len(capabilities)} capabilities found")
    kinds = {c.capability_type for c in capabilities}
    if superspeed:
        for required in (DeviceCapabilityType.USB20_EXTENSION, DeviceCapabilityType.SUPERSPEED_USB):
            if required not in kinds:
                findings.append(f"a SuperSpeed device must report the {required.name} capability")
    for capability in capabilities:
        if capability.capability_type == DeviceCapabilityType.USB20_EXTENSION:
            findings += _check_usb20_extension(capability.raw)
    return findings


# TD 9.7 step 11 predates the LPM errata, which defined bits 4:2 (BESL support) and 15:8
# (baseline/deep BESL) that TD 9.21 relies on; only the bits still reserved are checked.
USB20_EXTENSION_RESERVED = 0xFFFF00E0


def _check_usb20_extension(raw: bytes) -> Findings:
    if raw[0] != 7 or len(raw) != 7:
        return [f"USB 2.0 Extension bLength is {raw[0]}, expected 7"]
    findings: Findings = []
    attributes = int.from_bytes(raw[3:7], "little")
    if attributes & 0x1:
        findings.append("USB 2.0 Extension bmAttributes bit 0 must be 0")
    if not attributes & 0x2:
        findings.append("USB 2.0 Extension bmAttributes bit 1 (LPM) must be 1")
    if attributes & USB20_EXTENSION_RESERVED:
        findings.append(f"USB 2.0 Extension bmAttributes 0x{attributes:08X}: reserved bits set")
    return findings


# TD 9.8 String Descriptor Test -------------------------------------------------------------

def check_language_table(data: bytes) -> Findings:
    """TD 9.8 step 4: string descriptor zero, requested with wLength 500."""
    if len(data) < 2:
        return [f"string descriptor 0 returned {len(data)} bytes"]
    table = StringDescriptor(data)
    findings: Findings = []
    if len(data) < 4:
        findings.append(f"LANGID table has {len(data)} bytes; at least one LANGID (4 bytes) is required")
    if len(data) % 2:
        findings.append(f"LANGID table length {len(data)} is not a multiple of 2")
    if table.type_code != DescriptorType.STRING:
        findings.append(f"bDescriptorType is {table.type_code}, expected STRING (3)")
    if table.length != len(data):
        findings.append(f"bLength is {table.length} but {len(data)} bytes were returned")
    return findings


def check_string(data: bytes, index: int, language_id: int) -> Findings:
    """TD 9.8 step 6."""
    name = f"string {index} (LANGID 0x{language_id:04X})"
    if len(data) < 2:
        return [f"{name}: returned {len(data)} bytes, at least 2 required"]
    findings: Findings = []
    if data[1] != DescriptorType.STRING:
        findings.append(f"{name}: bDescriptorType is {data[1]}, expected STRING (3)")
    if data[0] != len(data):
        findings.append(f"{name}: bLength is {data[0]} but {len(data)} bytes were returned")
    return findings


def string_indices(device: DeviceDescriptor, configurations: list[bytes]) -> list[int]:
    """Every non-zero string index named by the device and configuration descriptors."""
    indices = set(device.string_indices)
    for data in configurations:
        try:
            indices |= parse_configuration(data).string_indices
        except DescriptorError:
            pass
    return sorted(indices)


# TD 9.18 Device Qualifier Descriptor Test --------------------------------------------------

def check_device_qualifier(data: bytes, speed: Speed = Speed.FULL) -> Findings:
    """TD 9.18 step 3, for a high-speed capable device that returned a qualifier."""
    if len(data) != DeviceQualifierDescriptor.SIZE:
        return [f"device qualifier has {len(data)} bytes, expected 10"]
    q = DeviceQualifierDescriptor.from_bytes(data)
    findings: Findings = []
    if q.bcd_usb >> 8 != 0x02:
        findings.append(f"bcdUSB is 0x{q.bcd_usb:04X}, the high byte must be 02")
    if q.length != 10:
        findings.append(f"bLength is {q.length}, expected 10")
    if q.type_code != DescriptorType.DEVICE_QUALIFIER:
        findings.append(f"bDescriptorType is {q.type_code}, expected DEVICE_QUALIFIER (6)")
    if q.device_class == 0 and q.device_subclass != 0:
        findings.append("bDeviceClass is 0 but bDeviceSubClass is not")
    expected = FULL_SPEED_EP0_SIZES if speed is Speed.HIGH else (64,)
    if q.max_packet_size0 not in expected:
        findings.append(f"bMaxPacketSize0 is {q.max_packet_size0}, expected one of {expected}")
    if q.num_configurations == 0:
        findings.append("bNumConfigurations is 0")
    # The test specification prints "bReserved != 0"; USB 2.0 table 9-9 requires zero.
    if q.reserved != 0:
        findings.append(f"bReserved is {q.reserved}, must be 0")
    return findings


# TD 9.30 Configuration Summary Descriptors Test (USB 3.2 CV spec) --------------------------

def function_signatures(data: bytes) -> set[tuple[int, int, int]]:
    """TD 9.30 step 11: one class/subclass/protocol signature per USB function."""
    parsed = parse_configuration(data)
    signatures = set()
    associated: set[int] = set()
    for _, iad in parsed.associations:
        signatures.add((iad.function_class, iad.function_subclass, iad.function_protocol))
        associated |= set(iad.interfaces)
    for interface in parsed.interfaces:
        if interface.alternate_setting == 0 and interface.interface_number not in associated:
            signatures.add((interface.interface_class, interface.interface_subclass,
                            interface.interface_protocol))
    return signatures


def check_configuration_summaries(summaries: list[bytes], configurations: list[bytes]) -> Findings:
    """TD 9.30 steps 5b and 13 to 14."""
    findings: Findings = []
    if summaries and len(configurations) == 1:
        findings.append("a single-configuration device must not have Configuration Summary descriptors")
    per_config = [function_signatures(data) for data in configurations]
    in_all = set.intersection(*per_config) if per_config else set()
    summary_signatures = set()
    for raw in summaries:
        if len(raw) < 9:
            findings.append(f"Configuration Summary of {len(raw)} bytes is too short")
            continue
        signature = (raw[5], raw[6], raw[7])
        count = raw[8]
        summary_signatures.add(signature)
        if raw[0] != 9 + count:
            findings.append(f"Configuration Summary bLength {raw[0]} != 9 + bConfigurationCount {count}")
        if raw[1] != DescriptorType.DEVICE_CAPABILITY or raw[2] != DeviceCapabilityType.CONFIGURATION_SUMMARY:
            findings.append("Configuration Summary has the wrong bDescriptorType/bDevCapabilityType")
        if int.from_bytes(raw[3:5], "little") != 0x0100:
            findings.append("Configuration Summary bcdVersion is not 0x0100")
        if not any(signature in s for s in per_config):
            findings.append(f"Configuration Summary signature {signature} matches no USB function")
        for index in raw[9:9 + count]:
            if index >= len(per_config) or signature not in per_config[index]:
                findings.append(f"Configuration Summary {signature} names configuration index {index} "
                                "which has no such function")
    for signatures in per_config:
        for signature in signatures - in_all - summary_signatures:
            findings.append(f"function {signature} is not in every configuration and has no "
                            "Configuration Summary descriptor")
    return sorted(set(findings), key=findings.index)


def descriptor_types(data: bytes) -> Counter:
    """How many descriptors of each type a set holds (used for coverage and logging)."""
    return Counter(getattr(d, "type_code", d.descriptor_type) for d in iter_descriptors(data))
