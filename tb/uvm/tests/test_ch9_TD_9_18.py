"""TD 9.18: Device Qualifier Descriptor Test (USB 2.0 CV specification, section 3.1.18).

In the Default, Address and Configured states (Configured once per configuration):
GET_DESCRIPTOR(DEVICE_QUALIFIER). A full-speed-only device must answer with a request
error (USB 2.0 section 9.6.2); a device that returns a qualifier claims high-speed
capability and the step 3 checks apply. The specification requests 9 bytes but checks
bLength == 10 and all ten fields; 10 bytes are requested so every field can be checked.
"""
import pyuvm

from ch9_base_test import SIM_TIMEOUT_MS, Ch9BaseTest
from usbtb.ch9 import usbcv
from usbtb.ch9.constants import DescriptorType, DeviceState
from usbtb.seq.ch9_base import Ch9Sequence


class DeviceQualifierSeq(Ch9Sequence):
    """The device qualifier descriptor in every device state."""

    td = "TD 9.18"

    async def procedure(self):
        await self.for_each_state(self.check_qualifier)

    async def check_qualifier(self, state: DeviceState, _: int) -> None:
        result = await self.host.get_descriptor(DescriptorType.DEVICE_QUALIFIER, 0, 10)
        if result.stalled:
            self.info("DEVICE_QUALIFIER is a request error: full-speed-only device")
            return
        self.require(result, "GET_DESCRIPTOR(DEVICE_QUALIFIER, 10)")
        self.check_findings(usbcv.check_device_qualifier(result.data), f"device qualifier ({state.value})")


@pyuvm.test(timeout_time=SIM_TIMEOUT_MS, timeout_unit="ms")
class TD_9_18_DeviceQualifierTest(Ch9BaseTest):
    sequence = DeviceQualifierSeq
