"""TD 9.1: Device Descriptor Test (USB 2.0 CV specification, section 3.1.1).

GET_DESCRIPTOR(DEVICE, 18) in the Default, Address and Configured states (the Configured
state once per configuration), with the step 3 content checks on every answer.

The idVendor check needs the USB-IF vendor list, which is not available here; it is
reported as not performed.
"""
import pyuvm

from ch9_base_test import SIM_TIMEOUT_MS, Ch9BaseTest
from usbtb.ch9 import usbcv
from usbtb.ch9.constants import DeviceState
from usbtb.seq.ch9_base import Ch9Sequence


class DeviceDescriptorSeq(Ch9Sequence):
    """Device descriptor content in every device state."""

    td = "TD 9.1"

    async def procedure(self):
        self.info("idVendor is not checked against the USB-IF list (not available)")
        await self.for_each_state(self.check_device_descriptor)

    async def check_device_descriptor(self, state: DeviceState, configuration_index: int) -> None:
        result = self.require(await self.host.get_device_descriptor(18), "GET_DESCRIPTOR(DEVICE, 18)")
        self.check_findings(usbcv.check_device_descriptor(result.data),
                            f"device descriptor in the {state.value} state (step 3)")


@pyuvm.test(timeout_time=SIM_TIMEOUT_MS, timeout_unit="ms")
class TD_9_1_DeviceDescriptorTest(Ch9BaseTest):
    sequence = DeviceDescriptorSeq
