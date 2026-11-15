"""The eight-byte SETUP packet (USB 2.0 section 9.3) and standard request factories."""
from __future__ import annotations

import struct
from dataclasses import dataclass

from .constants import (
    DescriptorType,
    Direction,
    FeatureSelector,
    Recipient,
    Request,
    RequestType,
)

_SETUP_FORMAT = "<BBHHH"


@dataclass(frozen=True, slots=True)
class SetupPacket:
    bm_request_type: int
    b_request: int
    w_value: int = 0
    w_index: int = 0
    w_length: int = 0

    def __post_init__(self) -> None:
        for name, limit in (("bm_request_type", 0xFF), ("b_request", 0xFF), ("w_value", 0xFFFF),
                            ("w_index", 0xFFFF), ("w_length", 0xFFFF)):
            if not 0 <= getattr(self, name) <= limit:
                raise ValueError(f"{name} out of range: {getattr(self, name)}")

    @classmethod
    def from_bytes(cls, data: bytes) -> SetupPacket:
        if len(data) != 8:
            raise ValueError(f"a SETUP packet has 8 bytes, got {len(data)}")
        return cls(*struct.unpack(_SETUP_FORMAT, data))

    def to_bytes(self) -> bytes:
        return struct.pack(_SETUP_FORMAT, self.bm_request_type, self.b_request,
                           self.w_value, self.w_index, self.w_length)

    @property
    def direction(self) -> Direction:
        return Direction(self.bm_request_type >> 7)

    @property
    def type(self) -> RequestType | int:
        value = (self.bm_request_type >> 5) & 0x3
        return RequestType(value) if value in RequestType._value2member_map_ else value

    @property
    def recipient(self) -> Recipient | int:
        value = self.bm_request_type & 0x1F
        return Recipient(value) if value in Recipient._value2member_map_ else value

    @property
    def request(self) -> Request | int:
        if self.type is not RequestType.STANDARD:
            return self.b_request
        return Request(self.b_request) if self.b_request in Request._value2member_map_ else self.b_request

    @property
    def descriptor_type(self) -> int:
        return self.w_value >> 8

    @property
    def descriptor_index(self) -> int:
        return self.w_value & 0xFF

    @property
    def has_data_stage(self) -> bool:
        return self.w_length > 0

    def __str__(self) -> str:
        name = self.request.name if isinstance(self.request, Request) else f"bRequest=0x{self.b_request:02X}"
        return (f"{name}(bmRequestType=0x{self.bm_request_type:02X}, wValue=0x{self.w_value:04X}, "
                f"wIndex=0x{self.w_index:04X}, wLength={self.w_length})")

    # Standard request factories (USB 2.0 section 9.4) -------------------------------------

    @staticmethod
    def request_type(direction: Direction, recipient: Recipient,
                     kind: RequestType = RequestType.STANDARD) -> int:
        return (direction << 7) | (kind << 5) | recipient

    @classmethod
    def get_descriptor(cls, descriptor_type: int, index: int = 0, length: int = 0xFF,
                       language_id: int = 0) -> SetupPacket:
        return cls(cls.request_type(Direction.IN, Recipient.DEVICE), Request.GET_DESCRIPTOR,
                   (descriptor_type << 8) | index, language_id, length)

    @classmethod
    def set_address(cls, address: int) -> SetupPacket:
        return cls(cls.request_type(Direction.OUT, Recipient.DEVICE), Request.SET_ADDRESS, address)

    @classmethod
    def get_configuration(cls) -> SetupPacket:
        return cls(cls.request_type(Direction.IN, Recipient.DEVICE), Request.GET_CONFIGURATION, 0, 0, 1)

    @classmethod
    def set_configuration(cls, value: int) -> SetupPacket:
        return cls(cls.request_type(Direction.OUT, Recipient.DEVICE), Request.SET_CONFIGURATION, value)

    @classmethod
    def get_interface(cls, interface: int) -> SetupPacket:
        return cls(cls.request_type(Direction.IN, Recipient.INTERFACE), Request.GET_INTERFACE, 0, interface, 1)

    @classmethod
    def set_interface(cls, interface: int, alternate: int) -> SetupPacket:
        return cls(cls.request_type(Direction.OUT, Recipient.INTERFACE), Request.SET_INTERFACE,
                   alternate, interface)

    @classmethod
    def get_status(cls, recipient: Recipient = Recipient.DEVICE, index: int = 0) -> SetupPacket:
        return cls(cls.request_type(Direction.IN, recipient), Request.GET_STATUS, 0, index, 2)

    @classmethod
    def set_feature(cls, selector: FeatureSelector | int, recipient: Recipient = Recipient.DEVICE,
                    index: int = 0) -> SetupPacket:
        return cls(cls.request_type(Direction.OUT, recipient), Request.SET_FEATURE, selector, index)

    @classmethod
    def clear_feature(cls, selector: FeatureSelector | int, recipient: Recipient = Recipient.DEVICE,
                      index: int = 0) -> SetupPacket:
        return cls(cls.request_type(Direction.OUT, recipient), Request.CLEAR_FEATURE, selector, index)


def device_descriptor_request(length: int = 18) -> SetupPacket:
    return SetupPacket.get_descriptor(DescriptorType.DEVICE, length=length)


def configuration_descriptor_request(index: int, length: int) -> SetupPacket:
    return SetupPacket.get_descriptor(DescriptorType.CONFIGURATION, index, length)
