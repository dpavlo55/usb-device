"""TD 9.3: Interface Association Descriptor Test (USB 3.2 CV specification v1.22, TD.9.3).

In the Default, Address and Configured states, for every configuration: the header
(bLength 9), the full set, then for every IAD: bDescriptorType 11, bInterfaceCount naming
contiguous interfaces, bFunctionClass not zero, and TD 9.8 on its iFunction string.

The test constrains the random device to a composite device with at least one IAD, so the
checks always have something to check.
"""
import pyuvm

from ch9_base_test import SIM_TIMEOUT_MS, Ch9BaseTest
from usbtb.ch9 import usbcv
from usbtb.ch9.constants import DeviceState
from usbtb.ch9.descriptors import DescriptorError
from usbtb.ch9.profile import has_associations
from usbtb.seq.ch9_base import Ch9Sequence
from usbtb.seq.ch9_steps import string_steps


class InterfaceAssociationSeq(Ch9Sequence):
    """Interface association descriptors in every device state."""

    td = "TD 9.3"

    async def procedure(self):
        await self.for_each_state(self.check_associations, each_configuration=False)

    async def check_associations(self, state: DeviceState, _: int) -> None:
        for index in range(len(self.enumeration.configurations)):
            header = self.require(await self.host.get_configuration_descriptor(index, 9),
                                  f"GET_DESCRIPTOR(CONFIGURATION {index}, 9)")
            self.check(header.data[:1] == b"\x09", f"configuration {index}: bLength is not 9 (step 2)")
            data = await self.read_configuration(index)
            if data is None:
                continue
            self.check_findings(usbcv.check_interface_associations(data), f"configuration {index} IADs (step 5)")
            try:
                associations = usbcv.parse_configuration(data).associations
            except DescriptorError:
                continue
            if not associations:
                self.info(f"configuration {index} has no interface association descriptor")
            functions = sorted({iad.i_function for _, iad in associations if iad.i_function})
            await string_steps(self, functions)


@pyuvm.test(timeout_time=SIM_TIMEOUT_MS, timeout_unit="ms")
class TD_9_3_InterfaceAssociationTest(Ch9BaseTest):
    sequence = InterfaceAssociationSeq
    profile_filter = staticmethod(has_associations)
