"""Constrained-random invalid requests for the "bad request" tests (TD 9.10 and TD 9.11).

USBCV: "Invalid means that it is not within the set of defined values in the USB 2.0
specification or any class specifications." The constraints exclude every value defined
by USB 2.0, its ECNs (IAD, BOS/LPM, OTG), USB 3.x and the class specifications (class
descriptor types 0x20 to 0x3F).
"""
from __future__ import annotations

import vsc

from ..ch9.constants import DescriptorType, FeatureSelector, Recipient
from ..ch9.setup import SetupPacket
from ..common.seeding import seeded


@vsc.randobj
class InvalidDescriptorRequest:
    def __init__(self):
        self.descriptor_type = vsc.rand_uint8_t()
        self.index = vsc.rand_uint8_t()
        self.length = vsc.rand_uint16_t()

    @vsc.constraint
    def undefined_type(self):
        # 0x00, then 0x13..0x1F (above the USB 3.x types), then 0x40..0xFE (above the
        # class-specific range). 0x0C..0x0E (wireless USB) and 0x10..0x12 are avoided.
        self.descriptor_type.inside(vsc.rangelist(0x00, vsc.rng(0x13, 0x1F), vsc.rng(0x40, 0xFE)))
        self.index <= 3
        self.length.inside(vsc.rangelist(vsc.rng(1, 255)))

    def setup(self) -> SetupPacket:
        return SetupPacket.get_descriptor(int(self.descriptor_type), int(self.index), int(self.length))


@vsc.randobj
class InvalidFeatureRequest:
    def __init__(self):
        self.selector = vsc.rand_uint16_t()

    @vsc.constraint
    def undefined_selector(self):
        # Defined: 0-2 (USB 2.0), 3-5 (OTG), 6 (wireless debug mode), 48-50 (USB 3.x link).
        self.selector.inside(vsc.rangelist(vsc.rng(7, 47), vsc.rng(51, 0xFFFF)))
        vsc.dist(self.selector, [vsc.weight(vsc.rng(7, 47), 3), vsc.weight(vsc.rng(51, 255), 2),
                                 vsc.weight(vsc.rng(256, 0xFFFF), 1)])

    def set_feature(self) -> SetupPacket:
        return SetupPacket.set_feature(int(self.selector), Recipient.DEVICE)

    def clear_feature(self) -> SetupPacket:
        return SetupPacket.clear_feature(int(self.selector), Recipient.DEVICE)


def invalid_descriptor_requests(seed: int, count: int) -> list[SetupPacket]:
    request = seeded(InvalidDescriptorRequest(), seed, "invalid_descriptor")
    packets = []
    for _ in range(count):
        request.randomize()
        packets.append(request.setup())
    return packets


def invalid_feature_selectors(seed: int, count: int) -> list[InvalidFeatureRequest]:
    request = seeded(InvalidFeatureRequest(), seed, "invalid_feature")
    selectors = []
    for _ in range(count):
        request.randomize()
        selectors.append(int(request.selector))
    return selectors


DEFINED_DESCRIPTOR_TYPES = frozenset(int(t) for t in DescriptorType)
DEFINED_FEATURE_SELECTORS = frozenset(int(f) for f in FeatureSelector) | {3, 4, 5, 6, 48, 49, 50}
