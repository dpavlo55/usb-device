"""TD 9.4: Interface Descriptor Test (USB 2.0 CV specification, section 3.1.4).

In the Default, Address and Configured states, for every configuration: set it, check the
default-interface bandwidth rule and the interface numbering/content rules, then select
every alternate setting with SET_INTERFACE and read it back with GET_INTERFACE.

The test constrains the random device to have alternate settings.
"""
import pyuvm

from ch9_base_test import SIM_TIMEOUT_MS, Ch9BaseTest
from usbtb.ch9.constants import DeviceState
from usbtb.ch9.profile import has_alternate_settings
from usbtb.seq.ch9_base import Ch9Sequence
from usbtb.seq.ch9_steps import interface_steps


class InterfaceDescriptorSeq(Ch9Sequence):
    """Interface descriptors and alternate settings in every device state."""

    td = "TD 9.4"

    async def procedure(self):
        await self.for_each_state(self.check_interfaces, each_configuration=False)

    async def check_interfaces(self, state: DeviceState, _: int) -> None:
        for index in range(len(self.enumeration.configurations)):
            await interface_steps(self, index)


@pyuvm.test(timeout_time=SIM_TIMEOUT_MS, timeout_unit="ms")
class TD_9_4_InterfaceDescriptorTest(Ch9BaseTest):
    sequence = InterfaceDescriptorSeq
    profile_filter = staticmethod(has_alternate_settings)
