"""TD 9.8: String Descriptor Test (USB 2.0 CV specification, section 3.1.8).

In the Default, Address and Configured states (Configured once per configuration): read
the LANGID table with wLength 500 and check it, then read every string index named by the
device, configuration, interface and IAD descriptors in every language and check it.

The test constrains the random device to have string descriptors.
"""
import pyuvm

from ch9_base_test import SIM_TIMEOUT_MS, Ch9BaseTest
from usbtb.ch9.constants import DeviceState
from usbtb.ch9.profile import has_strings
from usbtb.seq.ch9_base import Ch9Sequence
from usbtb.seq.ch9_steps import all_string_indices, string_steps


class StringDescriptorSeq(Ch9Sequence):
    """String descriptors in every device state and language."""

    td = "TD 9.8"

    async def procedure(self):
        await self.for_each_state(self.check_strings)

    async def check_strings(self, state: DeviceState, _: int) -> None:
        indices = await all_string_indices(self)
        self.info(f"string indices referenced: {indices}")
        await string_steps(self, indices)


@pyuvm.test(timeout_time=SIM_TIMEOUT_MS, timeout_unit="ms")
class TD_9_8_StringDescriptorTest(Ch9BaseTest):
    sequence = StringDescriptorSeq
    profile_filter = staticmethod(has_strings)
