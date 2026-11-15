"""TD 9.9: Halt Endpoint Test (USB 2.0 CV specification, section 3.1.9).

In the Configured state, for every configuration: the TD 9.4 interface steps, then for
every bulk and interrupt endpoint of each alternate setting: GET_STATUS, SET_FEATURE
(ENDPOINT_HALT), GET_STATUS reports halt, CLEAR_FEATURE(ENDPOINT_HALT), GET_STATUS does
not. Beyond USBCV, the endpoint is probed with a token after each step: STALL while
halted, no STALL once cleared.

The test constrains the random device to have bulk or interrupt endpoints.
"""
import pyuvm

from ch9_base_test import SIM_TIMEOUT_MS, Ch9BaseTest
from usbtb.ch9.constants import DeviceState
from usbtb.ch9.profile import has_halt_endpoints
from usbtb.seq.ch9_base import Ch9Sequence
from usbtb.seq.ch9_steps import interface_steps


class HaltEndpointSeq(Ch9Sequence):
    """ENDPOINT_HALT on every bulk and interrupt endpoint."""

    td = "TD 9.9"

    async def procedure(self):
        await self.for_each_state(self.halt_endpoints, states=(DeviceState.CONFIGURED,), each_configuration=False)

    async def halt_endpoints(self, state: DeviceState, _: int) -> None:
        for index in range(len(self.enumeration.configurations)):
            await interface_steps(self, index, halt_endpoints=True)


@pyuvm.test(timeout_time=SIM_TIMEOUT_MS, timeout_unit="ms")
class TD_9_9_HaltEndpointTest(Ch9BaseTest):
    sequence = HaltEndpointSeq
    profile_filter = staticmethod(has_halt_endpoints)
