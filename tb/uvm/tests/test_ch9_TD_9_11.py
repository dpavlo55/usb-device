"""TD 9.11: Bad Feature Test (USB 2.0 CV specification, section 3.1.11).

In the Address state: SET_FEATURE and then CLEAR_FEATURE with an undefined feature
selector must each STALL, and the device must still answer GET_DESCRIPTOR(DEVICE) after
each (or it is re-enumerated, a failure). The selectors are constrained-random (pyvsc).
"""
import pyuvm

from ch9_base_test import SIM_TIMEOUT_MS, Ch9BaseTest
from usbtb.ch9.constants import DeviceState, Recipient
from usbtb.ch9.setup import SetupPacket
from usbtb.seq.ch9_base import Ch9Sequence
from usbtb.seq.random_requests import invalid_feature_selectors

SELECTORS_PER_RUN = 3


class BadFeatureSeq(Ch9Sequence):
    """SET_FEATURE and CLEAR_FEATURE with undefined feature selectors."""

    td = "TD 9.11"

    async def procedure(self):
        await self.enter_state(DeviceState.ADDRESS)
        for selector in invalid_feature_selectors(self.cfg.seed, SELECTORS_PER_RUN):
            for setup in (SetupPacket.set_feature(selector, Recipient.DEVICE),
                          SetupPacket.clear_feature(selector, Recipient.DEVICE)):
                self.expect_request_error(await self.host.control(setup), str(setup))
                await self.ensure_responding(DeviceState.ADDRESS, str(setup))


@pyuvm.test(timeout_time=SIM_TIMEOUT_MS, timeout_unit="ms")
class TD_9_11_BadFeatureTest(Ch9BaseTest):
    sequence = BadFeatureSeq
