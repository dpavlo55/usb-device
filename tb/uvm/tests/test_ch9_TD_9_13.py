"""TD 9.13: Set Configuration Test (USB 2.0 CV specification, section 3.1.13).

From the Address state: set configuration index 0, then for each configuration: set it,
set configuration 0, set it again, verifying every step with GET_CONFIGURATION.

(Step 9 of the specification says to verify "the configuration that was set in step 7";
after SET_CONFIGURATION(0) a device reports 0, USB 2.0 section 9.4.2, which is what is
checked.)
"""
import pyuvm

from ch9_base_test import SIM_TIMEOUT_MS, Ch9BaseTest
from usbtb.ch9.constants import DeviceState
from usbtb.ch9.descriptors import ConfigurationDescriptor
from usbtb.seq.ch9_base import Ch9Sequence


class SetConfigurationSeq(Ch9Sequence):
    """SET_CONFIGURATION and GET_CONFIGURATION for every configuration."""

    td = "TD 9.13"

    async def procedure(self):
        enumeration = await self.enter_state(DeviceState.ADDRESS)
        await self.set_and_verify_configuration(await self.configuration_value(0))  # steps 2-4
        for index in range(len(enumeration.configurations)):
            value = await self.configuration_value(index)  # step 5
            await self.set_and_verify_configuration(value)  # steps 6-7
            await self.set_and_verify_configuration(0)  # steps 8-9
            await self.set_and_verify_configuration(value)  # steps 10-11

    async def configuration_value(self, index: int) -> int:
        header = self.require(await self.host.get_configuration_descriptor(index, 9),
                              f"GET_DESCRIPTOR(CONFIGURATION {index}, 9)")
        return ConfigurationDescriptor.from_bytes(header.data).configuration_value


@pyuvm.test(timeout_time=SIM_TIMEOUT_MS, timeout_unit="ms")
class TD_9_13_SetConfigurationTest(Ch9BaseTest):
    sequence = SetConfigurationSeq
