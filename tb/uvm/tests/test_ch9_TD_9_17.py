"""TD 9.17: Other Speed Configuration Descriptor Test (USB 2.0 CV specification, section 3.1.17).

USBCV runs this test on high-speed capable devices: bcdUSB must be 02xx and every
OTHER_SPEED_CONFIGURATION descriptor must pass TD 9.2. A full-speed-only device has no
other speed: USB 2.0 section 9.6.2 requires it to answer that request with a request
error. So, in each state: if the request STALLs, the device is full-speed only and that is
the correct answer; if it returns data, the device claims high-speed capability and the
full TD 9.17 checks apply.
"""
import pyuvm

from ch9_base_test import SIM_TIMEOUT_MS, Ch9BaseTest
from usbtb.ch9.constants import DescriptorType, DeviceState
from usbtb.ch9.descriptors import DeviceDescriptor
from usbtb.seq.ch9_base import Ch9Sequence
from usbtb.seq.ch9_steps import configuration_descriptor_steps


class OtherSpeedConfigurationSeq(Ch9Sequence):
    """OTHER_SPEED_CONFIGURATION descriptors in every device state."""

    td = "TD 9.17"

    async def procedure(self):
        await self.for_each_state(self.check_other_speed, each_configuration=False)

    async def check_other_speed(self, state: DeviceState, _: int) -> None:
        device = self.require(await self.host.get_device_descriptor(18), "GET_DESCRIPTOR(DEVICE, 18)")
        bcd_usb = DeviceDescriptor.from_bytes(device.data).bcd_usb
        probe = await self.host.get_descriptor(DescriptorType.OTHER_SPEED_CONFIGURATION, 0, 9)
        if probe.stalled:
            self.info("OTHER_SPEED_CONFIGURATION is a request error: full-speed-only device")
            return
        self.require(probe, "GET_DESCRIPTOR(OTHER_SPEED_CONFIGURATION, 9)")
        self.check(bcd_usb >> 8 == 0x02, f"bcdUSB 0x{bcd_usb:04X}: a high-speed capable device reports 02xx (step 3)")
        for index in range(len(self.enumeration.configurations)):
            await configuration_descriptor_steps(self, index, other_speed=True)


@pyuvm.test(timeout_time=SIM_TIMEOUT_MS, timeout_unit="ms")
class TD_9_17_OtherSpeedConfigurationTest(Ch9BaseTest):
    sequence = OtherSpeedConfigurationSeq
