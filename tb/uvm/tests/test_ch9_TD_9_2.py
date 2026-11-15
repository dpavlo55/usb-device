"""TD 9.2: Configuration Descriptor Test (USB 2.0 CV specification, section 3.1.2).

In the Default, Address and Configured states, for every configuration index: the 9-byte
header, the full set (wTotalLength), its structure (interface and endpoint counts), the
bmAttributes/bMaxPower rules, and bMaxPower against the power source GET_STATUS reports.
"""
import pyuvm

from ch9_base_test import SIM_TIMEOUT_MS, Ch9BaseTest
from usbtb.ch9.constants import DeviceState
from usbtb.seq.ch9_base import Ch9Sequence
from usbtb.seq.ch9_steps import configuration_descriptor_steps


class ConfigurationDescriptorSeq(Ch9Sequence):
    """Configuration descriptors in every device state."""

    td = "TD 9.2"

    async def procedure(self):
        await self.for_each_state(self.check_configurations, each_configuration=False)

    async def check_configurations(self, state: DeviceState, _: int) -> None:
        for index in range(len(self.enumeration.configurations)):
            await configuration_descriptor_steps(self, index)


@pyuvm.test(timeout_time=SIM_TIMEOUT_MS, timeout_unit="ms")
class TD_9_2_ConfigurationDescriptorTest(Ch9BaseTest):
    sequence = ConfigurationDescriptorSeq
