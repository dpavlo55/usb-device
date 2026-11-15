"""USB 2.0 Chapter 9 constants (USB 2.0 specification, section 9.4 and tables 9-4 to 9-7)."""
from __future__ import annotations

from enum import Enum, IntEnum, IntFlag


class Direction(IntEnum):
    """Data direction as seen from the host (bit 7 of bmRequestType and bEndpointAddress)."""

    OUT = 0
    IN = 1


class RequestType(IntEnum):
    STANDARD = 0
    CLASS = 1
    VENDOR = 2


class Recipient(IntEnum):
    DEVICE = 0
    INTERFACE = 1
    ENDPOINT = 2
    OTHER = 3


class Request(IntEnum):
    """Standard request codes, table 9-4."""

    GET_STATUS = 0
    CLEAR_FEATURE = 1
    SET_FEATURE = 3
    SET_ADDRESS = 5
    GET_DESCRIPTOR = 6
    SET_DESCRIPTOR = 7
    GET_CONFIGURATION = 8
    SET_CONFIGURATION = 9
    GET_INTERFACE = 10
    SET_INTERFACE = 11
    SYNCH_FRAME = 12


class DescriptorType(IntEnum):
    """Descriptor types, table 9-5, plus IAD (ECN) and the BOS types (USB 3.2 / USB 2.0 LPM ECN)."""

    DEVICE = 1
    CONFIGURATION = 2
    STRING = 3
    INTERFACE = 4
    ENDPOINT = 5
    DEVICE_QUALIFIER = 6
    OTHER_SPEED_CONFIGURATION = 7
    INTERFACE_POWER = 8
    OTG = 9
    DEBUG = 10
    INTERFACE_ASSOCIATION = 11
    BOS = 15
    DEVICE_CAPABILITY = 16


class DeviceCapabilityType(IntEnum):
    USB20_EXTENSION = 0x02
    SUPERSPEED_USB = 0x03
    CONTAINER_ID = 0x04
    CONFIGURATION_SUMMARY = 0x10


class FeatureSelector(IntEnum):
    """Standard feature selectors, table 9-6."""

    ENDPOINT_HALT = 0
    DEVICE_REMOTE_WAKEUP = 1
    TEST_MODE = 2


class TransferType(IntEnum):
    """bmAttributes bits 1:0 of an endpoint descriptor."""

    CONTROL = 0
    ISOCHRONOUS = 1
    BULK = 2
    INTERRUPT = 3


class ConfigAttributes(IntFlag):
    """bmAttributes of a configuration descriptor."""

    RESERVED_ONE = 0x80
    SELF_POWERED = 0x40
    REMOTE_WAKEUP = 0x20


class DeviceStatus(IntFlag):
    """GET_STATUS(device) response bits, figure 9-4."""

    SELF_POWERED = 0x01
    REMOTE_WAKEUP = 0x02


class EndpointStatus(IntFlag):
    """GET_STATUS(endpoint) response bits, figure 9-6."""

    HALT = 0x01


class DeviceState(Enum):
    """The visible device states of figure 9-1 (Powered and Attached collapse into DETACHED)."""

    DETACHED = "detached"
    DEFAULT = "default"
    ADDRESS = "address"
    CONFIGURED = "configured"


class Speed(Enum):
    LOW = "low"
    FULL = "full"
    HIGH = "high"


MAX_USB_ADDRESS = 127
FULL_SPEED_EP0_SIZES = (8, 16, 32, 64)
LANGID_US_ENGLISH = 0x0409

# Base class codes that may appear in a device descriptor (usb.org "Defined Class Codes",
# "Descriptor Usage" column = Device or Both).
DEVICE_CLASS_CODES = frozenset({0x00, 0x02, 0x09, 0x11, 0xDC, 0xEF, 0xFF})
