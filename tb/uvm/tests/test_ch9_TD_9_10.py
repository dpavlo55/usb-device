"""TD 9.10: Bad Descriptor Test (USB 2.0 CV specification, section 3.1.10).

In the Address state: GET_DESCRIPTOR with an undefined descriptor type must STALL; the
device must then still answer GET_DESCRIPTOR(DEVICE), or it is re-enumerated (a failure).
The undefined types, indices and wLength values are constrained-random (pyvsc), several
per run.
"""
import pyuvm

from ch9_base_test import SIM_TIMEOUT_MS, Ch9BaseTest
from usbtb.ch9.constants import DeviceState
from usbtb.seq.ch9_base import Ch9Sequence
from usbtb.seq.random_requests import invalid_descriptor_requests

REQUESTS_PER_RUN = 4


class BadDescriptorSeq(Ch9Sequence):
    """GET_DESCRIPTOR with undefined descriptor types."""

    td = "TD 9.10"

    async def procedure(self):
        await self.enter_state(DeviceState.ADDRESS)
        for setup in invalid_descriptor_requests(self.cfg.seed, REQUESTS_PER_RUN):
            self.info(f"undefined descriptor type 0x{setup.descriptor_type:02X}")
            self.expect_request_error(await self.host.control(setup), str(setup))
            await self.ensure_responding(DeviceState.ADDRESS, str(setup))


@pyuvm.test(timeout_time=SIM_TIMEOUT_MS, timeout_unit="ms")
class TD_9_10_BadDescriptorTest(Ch9BaseTest):
    sequence = BadDescriptorSeq
