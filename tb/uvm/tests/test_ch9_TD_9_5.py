"""TD 9.5: Endpoint Descriptor Test (USB 2.0 CV specification, section 3.1.5).

In the Default, Address and Configured states, for every configuration: set it, read
bcdUSB, and check every endpoint descriptor against the generic rules and the full-speed
(CV_FULL_1X or CV_FULL_2X) limits of its transfer type.
"""
import pyuvm

from ch9_base_test import SIM_TIMEOUT_MS, Ch9BaseTest
from usbtb.ch9.constants import DeviceState
from usbtb.seq.ch9_base import Ch9Sequence
from usbtb.seq.ch9_steps import endpoint_steps


class EndpointDescriptorSeq(Ch9Sequence):
    """Endpoint descriptors in every device state."""

    td = "TD 9.5"

    async def procedure(self):
        await self.for_each_state(self.check_endpoints, each_configuration=False)

    async def check_endpoints(self, state: DeviceState, _: int) -> None:
        for index in range(len(self.enumeration.configurations)):
            await endpoint_steps(self, index)


@pyuvm.test(timeout_time=SIM_TIMEOUT_MS, timeout_unit="ms")
class TD_9_5_EndpointDescriptorTest(Ch9BaseTest):
    sequence = EndpointDescriptorSeq
