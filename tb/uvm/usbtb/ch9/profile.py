"""Constrained-random device profiles: a legal USB 2.0 full-speed descriptor set per seed.

The descriptor set is what the device software (the firmware model) serves, so every
seed tests the hardware with a different device. The constraints keep each profile
legal for USB 2.0 and within what the hardware can hold.

Randomization is layered: pyvsc objects choose the knobs of the device, of each
configuration, interface and endpoint (with the per-type USB rules as constraints), and
plain Python assembles the hierarchy while it tracks the hardware budget.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

import vsc

from ..common.seeding import seeded
from ..ral import ram_bytes
from .constants import (
    ConfigAttributes,
    Direction,
    FULL_SPEED_EP0_SIZES,
    LANGID_US_ENGLISH,
    TransferType,
)
from .descriptors import (
    Configuration,
    DescriptorSet,
    DeviceDescriptor,
    EndpointDescriptor,
    Interface,
    InterfaceAssociationDescriptor,
    InterfaceDescriptor,
)

OTHER_LANGUAGES = (0x0407, 0x040C, 0x0411, 0x0804)
WORDS = ("USB", "Device", "Full", "Speed", "NeoRV32", "Bridge", "Sensor", "Port", "Data", "Control")


@dataclass(frozen=True)
class HardwareLimits:
    """What the device core can hold: 16 endpoint entries (two are EP0) and the shared RAM."""

    endpoint_entries: int = 14
    buffer_bytes: int = field(default_factory=lambda: ram_bytes() - 2 * 64)

    @staticmethod
    def buffer_size(max_packet_size: int) -> int:
        """Bytes the firmware allocates for an endpoint: a power of two, at least 8."""
        size = 8
        while size < max_packet_size:
            size *= 2
        return size


@vsc.randobj
class DeviceKnobs:
    def __init__(self):
        self.usb_2_0 = vsc.rand_bit_t(1)
        self.max_packet_size0 = vsc.rand_uint8_t()
        self.num_configurations = vsc.rand_uint8_t()
        self.composite = vsc.rand_bit_t(1)
        self.vendor_specific = vsc.rand_bit_t(1)
        self.id_vendor = vsc.rand_uint16_t()
        self.id_product = vsc.rand_uint16_t()
        self.bcd_device = vsc.rand_uint16_t()
        self.num_languages = vsc.rand_uint8_t()
        self.with_strings = vsc.rand_bit_t(1)
        self.self_powered = vsc.rand_bit_t(1)
        """The device's physical power source: GET_STATUS reports it, every configuration follows it."""

    @vsc.constraint
    def legal(self):
        self.max_packet_size0.inside(vsc.rangelist(*FULL_SPEED_EP0_SIZES))
        self.num_configurations.inside(vsc.rangelist(vsc.rng(1, 3)))
        vsc.dist(self.num_configurations, [vsc.weight(1, 6), vsc.weight(2, 3), vsc.weight(3, 1)])
        self.id_vendor.inside(vsc.rangelist(vsc.rng(1, 0xFFFE)))
        self.num_languages.inside(vsc.rangelist(vsc.rng(1, 3)))
        vsc.dist(self.usb_2_0, [vsc.weight(0, 1), vsc.weight(1, 3)])
        vsc.dist(self.with_strings, [vsc.weight(0, 1), vsc.weight(1, 4)])
        with vsc.implies(self.composite == 1):
            self.vendor_specific == 0


@vsc.randobj
class ConfigurationKnobs:
    def __init__(self):
        self.num_interfaces = vsc.rand_uint8_t()
        self.remote_wakeup = vsc.rand_bit_t(1)
        self.max_power = vsc.rand_uint8_t()
        self.self_powered = vsc.bit_t(1)
        """Context from the device knobs (not randomized here)."""

    @vsc.constraint
    def legal(self):
        self.num_interfaces.inside(vsc.rangelist(vsc.rng(1, 4)))
        # TD 9.2: bus powered draws at most 500 mA, self powered at most 100 mA (2 mA units),
        # and bMaxPower == 0 requires the self-powered bit.
        with vsc.if_then(self.self_powered == 1):
            self.max_power <= 50
        with vsc.else_then:
            self.max_power.inside(vsc.rangelist(vsc.rng(1, 250)))


@vsc.randobj
class InterfaceKnobs:
    def __init__(self):
        self.num_alternates = vsc.rand_uint8_t()
        self.interface_class = vsc.rand_uint8_t()
        self.interface_subclass = vsc.rand_uint8_t()
        self.interface_protocol = vsc.rand_uint8_t()
        self.num_endpoints = vsc.rand_uint8_t()

    @vsc.constraint
    def legal(self):
        self.num_alternates.inside(vsc.rangelist(vsc.rng(1, 3)))
        vsc.dist(self.num_alternates, [vsc.weight(1, 5), vsc.weight(2, 3), vsc.weight(3, 1)])
        self.interface_class.inside(vsc.rangelist(0x00, 0x02, 0x03, 0x08, 0x0A, 0xFE, 0xFF))
        vsc.dist(self.interface_class, [vsc.weight(0x00, 1), vsc.weight(0x02, 2), vsc.weight(0x03, 2),
                                        vsc.weight(0x08, 2), vsc.weight(0x0A, 2), vsc.weight(0xFE, 1),
                                        vsc.weight(0xFF, 3)])
        with vsc.implies(self.interface_class == 0):
            self.interface_subclass == 0
        self.num_endpoints <= 4


@vsc.randobj
class EndpointKnobs:
    """One endpoint descriptor; the full-speed limits of TD 9.5 are the constraints."""

    def __init__(self):
        self.transfer_type = vsc.rand_enum_t(TransferType)
        self.direction_in = vsc.rand_bit_t(1)
        self.max_packet_size = vsc.rand_uint16_t()
        self.interval = vsc.rand_uint8_t()
        self.iso_sync = vsc.rand_bit_t(2)
        self.iso_usage = vsc.rand_bit_t(2)
        # Context set by the generator before each randomize().
        self.alternate = vsc.uint8_t()
        self.usb_2_0 = vsc.bit_t(1)
        self.budget = vsc.uint16_t()

    @vsc.constraint
    def legal(self):
        self.transfer_type != TransferType.CONTROL
        self.max_packet_size >= 1
        self.max_packet_size <= self.budget
        with vsc.if_then(self.transfer_type == TransferType.BULK):
            self.max_packet_size.inside(vsc.rangelist(8, 16, 32, 64))
            self.interval == 0
        with vsc.else_if(self.transfer_type == TransferType.INTERRUPT):
            self.max_packet_size <= 64
            self.interval >= 1
        with vsc.else_then:
            # Isochronous: never in a default interface (TD 9.4 bandwidth rule), bInterval 1.
            self.alternate > 0
            self.max_packet_size <= 256
            self.interval == 1
            self.iso_usage < 3
        with vsc.implies(self.usb_2_0 == 0):
            self.iso_sync == 0
            self.iso_usage == 0


def endpoint_attributes(knobs: EndpointKnobs) -> int:
    """bmAttributes: transfer type, plus synchronization and usage type for isochronous."""
    attributes = int(knobs.transfer_type)
    if knobs.transfer_type is TransferType.ISOCHRONOUS:
        attributes |= (int(knobs.iso_sync) << 2) | (int(knobs.iso_usage) << 4)
    return attributes


class DeviceProfile:
    """Builds one random, legal :class:`DescriptorSet` from a seed."""

    def __init__(self, seed: int, limits: HardwareLimits = HardwareLimits()):
        self.seed = seed
        self.limits = limits
        self._rng = random.Random(f"{seed}:profile")
        self._device = seeded(DeviceKnobs(), seed, "device")
        self._configuration = seeded(ConfigurationKnobs(), seed, "configuration")
        self._interface = seeded(InterfaceKnobs(), seed, "interface")
        self._endpoint = seeded(EndpointKnobs(), seed, "endpoint")
        self._strings: dict[int, str] = {}

    def descriptor_set(self) -> DescriptorSet:
        device = self._device
        device.randomize()
        bcd_usb = 0x0200 if device.usb_2_0 else 0x0110
        configurations = [self._random_configuration(index + 1, bool(device.composite), bcd_usb)
                          for index in range(device.num_configurations)]
        if device.composite and any(c.associations for c in configurations):
            device_class = (0xEF, 0x02, 0x01)
        elif device.vendor_specific:
            device_class = (0xFF, self._rng.randrange(256), self._rng.randrange(256))
        else:
            device_class = (0x00, 0x00, 0x00)
        descriptor = DeviceDescriptor(
            bcd_usb=bcd_usb,
            device_class=device_class[0],
            device_subclass=device_class[1],
            device_protocol=device_class[2],
            max_packet_size0=int(device.max_packet_size0),
            id_vendor=int(device.id_vendor),
            id_product=int(device.id_product),
            bcd_device=int(device.bcd_device),
            i_manufacturer=self._string_index("Manufacturer"),
            i_product=self._string_index("Product"),
            i_serial_number=self._string_index("Serial"),
            num_configurations=len(configurations),
        )
        languages = (LANGID_US_ENGLISH, *self._rng.sample(OTHER_LANGUAGES, device.num_languages - 1))
        return DescriptorSet(descriptor, configurations, dict(self._strings), languages)

    def _string_index(self, purpose: str) -> int:
        if not self._device.with_strings or self._rng.random() < 0.25:
            return 0
        index = len(self._strings) + 1
        words = self._rng.sample(WORDS, self._rng.randint(1, 3))
        self._strings[index] = " ".join([purpose, *words])[: self._rng.randint(1, 60)]
        return index

    def _random_configuration(self, value: int, composite: bool, bcd_usb: int) -> Configuration:
        knobs = self._configuration
        knobs.self_powered = int(self._device.self_powered)
        knobs.randomize()
        attributes = ConfigAttributes.RESERVED_ONE
        if knobs.self_powered:
            attributes |= ConfigAttributes.SELF_POWERED
        if knobs.remote_wakeup:
            attributes |= ConfigAttributes.REMOTE_WAKEUP
        configuration = Configuration(value, int(attributes), int(knobs.max_power),
                                      self._string_index("Configuration"))
        endpoint_numbers = {direction: self._rng.sample(range(1, 16), 15) for direction in Direction}
        budget = _Budget(self.limits.endpoint_entries, self.limits.buffer_bytes)
        for number in range(knobs.num_interfaces):
            configuration.interfaces += self._random_interface(number, endpoint_numbers, budget, bcd_usb)
        if composite:
            configuration.associations = self._random_associations(configuration)
        return configuration

    def _random_interface(self, number: int, endpoint_numbers: dict[Direction, list[int]],
                          budget: _Budget, bcd_usb: int) -> list[Interface]:
        knobs = self._interface
        knobs.randomize()
        i_interface = self._string_index("Interface")
        alternates = []
        for alternate in range(knobs.num_alternates):
            endpoints = []
            for _ in range(knobs.num_endpoints if alternate == 0 else self._rng.randint(1, 3)):
                endpoint = self._random_endpoint(alternate, endpoint_numbers, budget, bcd_usb)
                if endpoint is None:
                    break
                endpoints.append(endpoint)
            descriptor = InterfaceDescriptor(number, alternate, len(endpoints), int(knobs.interface_class),
                                             int(knobs.interface_subclass), int(knobs.interface_protocol),
                                             i_interface)
            alternates.append(Interface(descriptor, endpoints))
        return alternates

    def _random_endpoint(self, alternate: int, endpoint_numbers: dict[Direction, list[int]],
                         budget: _Budget, bcd_usb: int) -> EndpointDescriptor | None:
        if budget.entries == 0 or budget.bytes < 8:
            return None
        knobs = self._endpoint
        knobs.alternate = alternate
        knobs.usb_2_0 = int(bcd_usb >= 0x0200)
        knobs.budget = min(budget.bytes, 1023)
        knobs.randomize()
        direction = Direction(int(knobs.direction_in))
        if not endpoint_numbers[direction]:
            return None
        address = endpoint_numbers[direction].pop() | (direction << 7)
        budget.take(self.limits.buffer_size(int(knobs.max_packet_size)))
        return EndpointDescriptor(address, endpoint_attributes(knobs), int(knobs.max_packet_size), int(knobs.interval))

    def _random_associations(self, configuration: Configuration) -> list[InterfaceAssociationDescriptor]:
        """Group contiguous interfaces into functions; functions of two or more get an IAD."""
        associations = []
        first = 0
        while first < configuration.num_interfaces:
            count = self._rng.randint(1, configuration.num_interfaces - first)
            if count >= 2:
                interface = configuration.alternates(first)[0].descriptor
                function_class = interface.interface_class or 0xFF
                associations.append(InterfaceAssociationDescriptor(
                    first, count, function_class, interface.interface_subclass,
                    interface.interface_protocol, self._string_index("Function")))
            first += count
        return associations


class _Budget:
    def __init__(self, entries: int, buffer_bytes: int):
        self.entries = entries
        self.bytes = buffer_bytes

    def take(self, buffer_bytes: int) -> None:
        self.entries -= 1
        self.bytes -= buffer_bytes


# Predicates for test-level constraints on a profile -------------------------------------------

def has_remote_wakeup(descriptors: DescriptorSet) -> bool:
    return any(c.header().remote_wakeup for c in descriptors.configurations)


def has_halt_endpoints(descriptors: DescriptorSet) -> bool:
    return any(ep.transfer_type in (TransferType.BULK, TransferType.INTERRUPT)
               for c in descriptors.configurations for ep in c.endpoints)


def has_associations(descriptors: DescriptorSet) -> bool:
    return any(c.associations for c in descriptors.configurations)


def has_alternate_settings(descriptors: DescriptorSet) -> bool:
    return any(i.descriptor.alternate_setting for c in descriptors.configurations for i in c.interfaces)


def has_strings(descriptors: DescriptorSet) -> bool:
    return bool(descriptors.strings)


def describe(descriptors: DescriptorSet) -> str:
    """A one-paragraph summary of a profile for the test log."""
    device = descriptors.device
    lines = [f"bcdUSB=0x{device.bcd_usb:04X} class=0x{device.device_class:02X} "
             f"bMaxPacketSize0={device.max_packet_size0} VID:PID={device.id_vendor:04X}:{device.id_product:04X} "
             f"strings={len(descriptors.strings)} langs={[hex(lang) for lang in descriptors.language_ids]}"]
    for configuration in descriptors.configurations:
        header = configuration.header()
        lines.append(f"  config {configuration.configuration_value}: {header.num_interfaces} interface(s), "
                     f"wTotalLength={header.total_length}, attributes=0x{header.attributes:02X}, "
                     f"bMaxPower={header.max_power}, IADs={len(configuration.associations)}")
        for interface in configuration.interfaces:
            d = interface.descriptor
            endpoints = ", ".join(f"0x{ep.endpoint_address:02X} {ep.transfer_type.name} mps={ep.max_packet_size}"
                                  for ep in interface.endpoints)
            lines.append(f"    if {d.interface_number} alt {d.alternate_setting} class=0x{d.interface_class:02X}"
                         f" [{endpoints}]")
    return "\n".join(lines)



