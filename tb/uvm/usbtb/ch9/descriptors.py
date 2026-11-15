"""Standard USB descriptors (USB 2.0 section 9.6): build them, serialize them and parse them back.

Every parsed descriptor keeps its ``length`` (``bLength``) and ``raw`` bytes, so compliance
checks can inspect exactly what a device sent, including malformed descriptors.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import ClassVar, Iterator

from .constants import (
    ConfigAttributes,
    DescriptorType,
    DeviceCapabilityType,
    Direction,
    LANGID_US_ENGLISH,
    TransferType,
)


class DescriptorError(ValueError):
    """Raised when bytes cannot be split into descriptors."""


@dataclass(frozen=True, slots=True)
class RawDescriptor:
    """A descriptor kept as bytes: class-specific, unknown, or too short to parse."""

    descriptor_type: int
    raw: bytes

    @property
    def length(self) -> int:
        return self.raw[0]

    def to_bytes(self) -> bytes:
        return self.raw


@dataclass(frozen=True, slots=True)
class DeviceDescriptor:
    FORMAT: ClassVar[str] = "<BBHBBBBHHHBBBB"
    SIZE: ClassVar[int] = 18
    descriptor_type: ClassVar[int] = DescriptorType.DEVICE

    bcd_usb: int
    device_class: int
    device_subclass: int
    device_protocol: int
    max_packet_size0: int
    id_vendor: int
    id_product: int
    bcd_device: int
    i_manufacturer: int
    i_product: int
    i_serial_number: int
    num_configurations: int
    length: int = SIZE
    type_code: int = DescriptorType.DEVICE

    def to_bytes(self) -> bytes:
        return struct.pack(self.FORMAT, self.length, self.type_code, self.bcd_usb, self.device_class,
                           self.device_subclass, self.device_protocol, self.max_packet_size0,
                           self.id_vendor, self.id_product, self.bcd_device, self.i_manufacturer,
                           self.i_product, self.i_serial_number, self.num_configurations)

    @classmethod
    def from_bytes(cls, data: bytes) -> DeviceDescriptor:
        (length, type_code, bcd_usb, device_class, subclass, protocol, mps0, vid, pid, bcd_device,
         i_man, i_prod, i_ser, num_cfg) = struct.unpack_from(cls.FORMAT, data)
        return cls(bcd_usb, device_class, subclass, protocol, mps0, vid, pid, bcd_device, i_man,
                   i_prod, i_ser, num_cfg, length, type_code)

    @property
    def string_indices(self) -> tuple[int, ...]:
        return tuple(i for i in (self.i_manufacturer, self.i_product, self.i_serial_number) if i)


@dataclass(frozen=True, slots=True)
class DeviceQualifierDescriptor:
    FORMAT: ClassVar[str] = "<BBHBBBBBB"
    SIZE: ClassVar[int] = 10
    descriptor_type: ClassVar[int] = DescriptorType.DEVICE_QUALIFIER

    bcd_usb: int
    device_class: int
    device_subclass: int
    device_protocol: int
    max_packet_size0: int
    num_configurations: int
    reserved: int = 0
    length: int = SIZE
    type_code: int = DescriptorType.DEVICE_QUALIFIER

    def to_bytes(self) -> bytes:
        return struct.pack(self.FORMAT, self.length, self.type_code, self.bcd_usb, self.device_class,
                           self.device_subclass, self.device_protocol, self.max_packet_size0,
                           self.num_configurations, self.reserved)

    @classmethod
    def from_bytes(cls, data: bytes) -> DeviceQualifierDescriptor:
        length, type_code, bcd, cls_, sub, proto, mps0, num, reserved = struct.unpack_from(cls.FORMAT, data)
        return cls(bcd, cls_, sub, proto, mps0, num, reserved, length, type_code)


@dataclass(frozen=True, slots=True)
class ConfigurationDescriptor:
    FORMAT: ClassVar[str] = "<BBHBBBBB"
    SIZE: ClassVar[int] = 9
    descriptor_type: ClassVar[int] = DescriptorType.CONFIGURATION

    total_length: int
    num_interfaces: int
    configuration_value: int
    i_configuration: int
    attributes: int
    max_power: int
    length: int = SIZE
    type_code: int = DescriptorType.CONFIGURATION

    def to_bytes(self) -> bytes:
        return struct.pack(self.FORMAT, self.length, self.type_code, self.total_length,
                           self.num_interfaces, self.configuration_value, self.i_configuration,
                           self.attributes, self.max_power)

    @classmethod
    def from_bytes(cls, data: bytes) -> ConfigurationDescriptor:
        length, type_code, total, num_if, value, i_cfg, attributes, power = struct.unpack_from(cls.FORMAT, data)
        return cls(total, num_if, value, i_cfg, attributes, power, length, type_code)

    @property
    def self_powered(self) -> bool:
        return bool(self.attributes & ConfigAttributes.SELF_POWERED)

    @property
    def remote_wakeup(self) -> bool:
        return bool(self.attributes & ConfigAttributes.REMOTE_WAKEUP)

    @property
    def max_power_ma(self) -> int:
        return 2 * self.max_power


@dataclass(frozen=True, slots=True)
class InterfaceAssociationDescriptor:
    FORMAT: ClassVar[str] = "<BBBBBBBB"
    SIZE: ClassVar[int] = 8
    descriptor_type: ClassVar[int] = DescriptorType.INTERFACE_ASSOCIATION

    first_interface: int
    interface_count: int
    function_class: int
    function_subclass: int
    function_protocol: int
    i_function: int
    length: int = SIZE
    type_code: int = DescriptorType.INTERFACE_ASSOCIATION

    def to_bytes(self) -> bytes:
        return struct.pack(self.FORMAT, self.length, self.type_code, self.first_interface,
                           self.interface_count, self.function_class, self.function_subclass,
                           self.function_protocol, self.i_function)

    @classmethod
    def from_bytes(cls, data: bytes) -> InterfaceAssociationDescriptor:
        length, type_code, first, count, f_cls, f_sub, f_proto, i_func = struct.unpack_from(cls.FORMAT, data)
        return cls(first, count, f_cls, f_sub, f_proto, i_func, length, type_code)

    @property
    def interfaces(self) -> range:
        return range(self.first_interface, self.first_interface + self.interface_count)


@dataclass(frozen=True, slots=True)
class InterfaceDescriptor:
    FORMAT: ClassVar[str] = "<BBBBBBBBB"
    SIZE: ClassVar[int] = 9
    descriptor_type: ClassVar[int] = DescriptorType.INTERFACE

    interface_number: int
    alternate_setting: int
    num_endpoints: int
    interface_class: int
    interface_subclass: int
    interface_protocol: int
    i_interface: int
    length: int = SIZE
    type_code: int = DescriptorType.INTERFACE

    def to_bytes(self) -> bytes:
        return struct.pack(self.FORMAT, self.length, self.type_code, self.interface_number,
                           self.alternate_setting, self.num_endpoints, self.interface_class,
                           self.interface_subclass, self.interface_protocol, self.i_interface)

    @classmethod
    def from_bytes(cls, data: bytes) -> InterfaceDescriptor:
        length, type_code, number, alt, num_ep, i_cls, i_sub, i_proto, i_if = struct.unpack_from(cls.FORMAT, data)
        return cls(number, alt, num_ep, i_cls, i_sub, i_proto, i_if, length, type_code)


@dataclass(frozen=True, slots=True)
class EndpointDescriptor:
    FORMAT: ClassVar[str] = "<BBBBHB"
    SIZE: ClassVar[int] = 7
    descriptor_type: ClassVar[int] = DescriptorType.ENDPOINT

    endpoint_address: int
    attributes: int
    max_packet_size: int
    interval: int
    length: int = SIZE
    type_code: int = DescriptorType.ENDPOINT

    def to_bytes(self) -> bytes:
        return struct.pack(self.FORMAT, self.length, self.type_code, self.endpoint_address,
                           self.attributes, self.max_packet_size, self.interval)

    @classmethod
    def from_bytes(cls, data: bytes) -> EndpointDescriptor:
        length, type_code, address, attributes, mps, interval = struct.unpack_from(cls.FORMAT, data)
        return cls(address, attributes, mps, interval, length, type_code)

    @property
    def number(self) -> int:
        return self.endpoint_address & 0x0F

    @property
    def direction(self) -> Direction:
        return Direction(self.endpoint_address >> 7)

    @property
    def transfer_type(self) -> TransferType:
        return TransferType(self.attributes & 0x3)

    @property
    def packet_size(self) -> int:
        """wMaxPacketSize bits 10:0 (bits 12:11 are the high-bandwidth multiplier)."""
        return self.max_packet_size & 0x7FF


@dataclass(frozen=True, slots=True)
class StringDescriptor:
    """String descriptor zero holds the LANGID table; the others hold UTF-16LE text."""

    raw: bytes
    descriptor_type: ClassVar[int] = DescriptorType.STRING

    @classmethod
    def from_text(cls, text: str) -> StringDescriptor:
        payload = text.encode("utf-16-le")
        return cls(bytes((2 + len(payload), DescriptorType.STRING)) + payload)

    @classmethod
    def language_table(cls, language_ids: tuple[int, ...] = (LANGID_US_ENGLISH,)) -> StringDescriptor:
        payload = b"".join(struct.pack("<H", lang) for lang in language_ids)
        return cls(bytes((2 + len(payload), DescriptorType.STRING)) + payload)

    @property
    def length(self) -> int:
        return self.raw[0]

    @property
    def type_code(self) -> int:
        return self.raw[1]

    @property
    def language_ids(self) -> tuple[int, ...]:
        payload = self.raw[2:]
        return tuple(struct.unpack_from("<H", payload, i)[0] for i in range(0, len(payload) - 1, 2))

    @property
    def text(self) -> str:
        return self.raw[2:].decode("utf-16-le", errors="replace")

    def to_bytes(self) -> bytes:
        return self.raw


@dataclass(frozen=True, slots=True)
class BosDescriptor:
    FORMAT: ClassVar[str] = "<BBHB"
    SIZE: ClassVar[int] = 5
    descriptor_type: ClassVar[int] = DescriptorType.BOS

    total_length: int
    num_device_caps: int
    length: int = SIZE
    type_code: int = DescriptorType.BOS

    def to_bytes(self) -> bytes:
        return struct.pack(self.FORMAT, self.length, self.type_code, self.total_length, self.num_device_caps)

    @classmethod
    def from_bytes(cls, data: bytes) -> BosDescriptor:
        length, type_code, total, num_caps = struct.unpack_from(cls.FORMAT, data)
        return cls(total, num_caps, length, type_code)


@dataclass(frozen=True, slots=True)
class DeviceCapabilityDescriptor:
    raw: bytes
    descriptor_type: ClassVar[int] = DescriptorType.DEVICE_CAPABILITY

    @property
    def length(self) -> int:
        return self.raw[0]

    @property
    def type_code(self) -> int:
        return self.raw[1]

    @property
    def capability_type(self) -> int:
        return self.raw[2] if len(self.raw) > 2 else -1

    def to_bytes(self) -> bytes:
        return self.raw

    @property
    def is_usb20_extension(self) -> bool:
        return self.capability_type == DeviceCapabilityType.USB20_EXTENSION


Descriptor = (DeviceDescriptor | DeviceQualifierDescriptor | ConfigurationDescriptor
              | InterfaceAssociationDescriptor | InterfaceDescriptor | EndpointDescriptor
              | StringDescriptor | BosDescriptor | DeviceCapabilityDescriptor | RawDescriptor)

_FIXED_LAYOUTS: dict[int, type] = {
    DescriptorType.DEVICE: DeviceDescriptor,
    DescriptorType.DEVICE_QUALIFIER: DeviceQualifierDescriptor,
    DescriptorType.CONFIGURATION: ConfigurationDescriptor,
    DescriptorType.OTHER_SPEED_CONFIGURATION: ConfigurationDescriptor,
    DescriptorType.INTERFACE_ASSOCIATION: InterfaceAssociationDescriptor,
    DescriptorType.INTERFACE: InterfaceDescriptor,
    DescriptorType.ENDPOINT: EndpointDescriptor,
    DescriptorType.BOS: BosDescriptor,
}


def parse_descriptor(data: bytes) -> Descriptor:
    """Parse one descriptor; ``data`` starts at its bLength byte and may hold more bytes."""
    if len(data) < 2:
        raise DescriptorError(f"a descriptor needs at least 2 bytes, got {len(data)}")
    length, type_code = data[0], data[1]
    body = bytes(data[:length])
    layout = _FIXED_LAYOUTS.get(type_code)
    if layout is not None and len(body) >= layout.SIZE:
        return layout.from_bytes(body)
    if type_code == DescriptorType.STRING:
        return StringDescriptor(body)
    if type_code == DescriptorType.DEVICE_CAPABILITY:
        return DeviceCapabilityDescriptor(body)
    return RawDescriptor(type_code, body)


def iter_descriptors(data: bytes) -> Iterator[Descriptor]:
    """Walk a descriptor set (a configuration or BOS hierarchy) by bLength."""
    offset = 0
    while offset < len(data):
        length = data[offset]
        if length < 2:
            raise DescriptorError(f"descriptor at offset {offset} has bLength {length}")
        if offset + length > len(data):
            raise DescriptorError(f"descriptor at offset {offset} (bLength {length}) runs past "
                                  f"the {len(data)} bytes returned")
        yield parse_descriptor(data[offset:offset + length])
        offset += length


# Builders for a device's full descriptor set ------------------------------------------------

@dataclass(slots=True)
class Interface:
    descriptor: InterfaceDescriptor
    endpoints: list[EndpointDescriptor] = field(default_factory=list)

    def to_bytes(self) -> bytes:
        return self.descriptor.to_bytes() + b"".join(ep.to_bytes() for ep in self.endpoints)


@dataclass(slots=True)
class Configuration:
    """A configuration and everything below it, in the order a device returns it."""

    configuration_value: int
    attributes: int
    max_power: int
    i_configuration: int = 0
    interfaces: list[Interface] = field(default_factory=list)
    associations: list[InterfaceAssociationDescriptor] = field(default_factory=list)

    @property
    def num_interfaces(self) -> int:
        return len({interface.descriptor.interface_number for interface in self.interfaces})

    @property
    def endpoints(self) -> list[EndpointDescriptor]:
        return [ep for interface in self.interfaces for ep in interface.endpoints]

    def alternates(self, interface_number: int) -> list[Interface]:
        return [i for i in self.interfaces if i.descriptor.interface_number == interface_number]

    def _body(self) -> bytes:
        associations = {iad.first_interface: iad for iad in self.associations}
        body = b""
        for interface in self.interfaces:
            descriptor = interface.descriptor
            if descriptor.alternate_setting == 0 and descriptor.interface_number in associations:
                body += associations[descriptor.interface_number].to_bytes()
            body += interface.to_bytes()
        return body

    def header(self, total_length: int | None = None) -> ConfigurationDescriptor:
        body_length = len(self._body())
        return ConfigurationDescriptor(
            total_length=ConfigurationDescriptor.SIZE + body_length if total_length is None else total_length,
            num_interfaces=self.num_interfaces,
            configuration_value=self.configuration_value,
            i_configuration=self.i_configuration,
            attributes=self.attributes,
            max_power=self.max_power,
        )

    def to_bytes(self) -> bytes:
        return self.header().to_bytes() + self._body()


@dataclass(slots=True)
class DescriptorSet:
    """Everything a device answers to GET_DESCRIPTOR."""

    device: DeviceDescriptor
    configurations: list[Configuration]
    strings: dict[int, str] = field(default_factory=dict)
    language_ids: tuple[int, ...] = (LANGID_US_ENGLISH,)

    def configuration(self, value: int) -> Configuration | None:
        return next((c for c in self.configurations if c.configuration_value == value), None)

    def string_bytes(self, index: int, language_id: int) -> bytes | None:
        if index == 0:
            return StringDescriptor.language_table(self.language_ids).to_bytes()
        if index not in self.strings or language_id not in self.language_ids:
            return None
        return StringDescriptor.from_text(self.strings[index]).to_bytes()

    def descriptor_bytes(self, descriptor_type: int, index: int, language_id: int) -> bytes | None:
        """The answer to GET_DESCRIPTOR, or ``None`` when the device must STALL."""
        match descriptor_type:
            case DescriptorType.DEVICE if index == 0:
                return self.device.to_bytes()
            case DescriptorType.CONFIGURATION if index < len(self.configurations):
                return self.configurations[index].to_bytes()
            case DescriptorType.STRING:
                return self.string_bytes(index, language_id)
            case _:
                # A full-speed-only device answers DEVICE_QUALIFIER, OTHER_SPEED_CONFIGURATION
                # and (with bcdUSB <= 0x0200) BOS with a request error (USB 2.0 section 9.6.2).
                return None
