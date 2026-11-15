"""TD 9.30: Configuration Summary Descriptors Test (USB 3.2 CV specification v1.22, TD.9.30).

The USB 2.0 CV specification adds one modification: "if the Device Descriptor field bcdUSB
<= 0x0200, then skip the remaining steps". In each state: GET_DESCRIPTOR(DEVICE, 5); for
bcdUSB above 0x0200, the BOS set is read, its Configuration Summary descriptors are found
and checked against the USB functions of every configuration (steps 5 to 14).
"""
import pyuvm

from ch9_base_test import SIM_TIMEOUT_MS, Ch9BaseTest
from usbtb.ch9 import usbcv
from usbtb.ch9.constants import DescriptorType, DeviceCapabilityType, DeviceState
from usbtb.ch9.descriptors import DescriptorError, DeviceCapabilityDescriptor, iter_descriptors
from usbtb.seq.ch9_base import Ch9Sequence


class ConfigurationSummarySeq(Ch9Sequence):
    """Configuration Summary descriptors of devices with bcdUSB above 0x0200."""

    td = "TD 9.30"

    async def procedure(self):
        await self.for_each_state(self.check_summaries, each_configuration=False)

    async def check_summaries(self, state: DeviceState, _: int) -> None:
        device = self.require(await self.host.get_device_descriptor(5), "GET_DESCRIPTOR(DEVICE, 5)")  # step 2
        bcd_usb = int.from_bytes(device.data[2:4], "little")
        if bcd_usb <= 0x0200:
            self.info(f"bcdUSB 0x{bcd_usb:04X}: remaining steps skipped")
            return
        num_configurations = len(self.enumeration.configurations)
        header = self.require(await self.host.get_descriptor(DescriptorType.BOS, 0, 5), "GET_DESCRIPTOR(BOS, 5)")
        total = int.from_bytes(header.data[2:4], "little")
        bos = self.require(await self.host.get_descriptor(DescriptorType.BOS, 0, total), "GET_DESCRIPTOR(BOS)")
        try:
            summaries = [d.raw for d in iter_descriptors(bos.data) if isinstance(d, DeviceCapabilityDescriptor)
                         and d.capability_type == DeviceCapabilityType.CONFIGURATION_SUMMARY]
        except DescriptorError as error:
            self.check(False, f"BOS set is malformed: {error}")
            return
        if not summaries:
            self.info("no Configuration Summary descriptors (step 5a)")
            return
        configurations = [await self.read_configuration(i) for i in range(num_configurations)]
        self.check_findings(usbcv.check_configuration_summaries(summaries, [c for c in configurations if c]),
                            "Configuration Summary descriptors (steps 13-14)")


@pyuvm.test(timeout_time=SIM_TIMEOUT_MS, timeout_unit="ms")
class TD_9_30_ConfigurationSummaryTest(Ch9BaseTest):
    sequence = ConfigurationSummarySeq
