"""TD 9.21: LPM L1 Suspend Resume Test (USB 2.0 CV specification, section 3.1.21).

In the Configured state: read bcdUSB. A device with bcdUSB 0x0200 or lower is not
required to support LPM and the test ends (step 3). For a newer device the USB 2.0
Extension capability must report BESL support (steps 6-7); the L1 transitions themselves
(steps 8-10) need an LPM-capable parent port, which this host model is not, so they are
reported as not performed, as USBCV does when "the parent port does not support LPM".
"""
import pyuvm

from ch9_base_test import SIM_TIMEOUT_MS, Ch9BaseTest
from usbtb.ch9.constants import DescriptorType, DeviceCapabilityType, DeviceState
from usbtb.ch9.descriptors import DeviceCapabilityDescriptor, DeviceDescriptor, iter_descriptors
from usbtb.seq.ch9_base import Ch9Sequence

BESL_SUPPORTED = 1 << 2


class LpmSeq(Ch9Sequence):
    """LPM support of devices with bcdUSB above 0x0200."""

    td = "TD 9.21"

    async def procedure(self):
        await self.enter_state(DeviceState.CONFIGURED)
        device = self.require(await self.host.get_device_descriptor(18), "GET_DESCRIPTOR(DEVICE, 18)")
        bcd_usb = DeviceDescriptor.from_bytes(device.data).bcd_usb
        if bcd_usb <= 0x0200:
            self.info(f"bcdUSB 0x{bcd_usb:04X}: LPM is not required (step 3)")
            return
        header = self.require(await self.host.get_descriptor(DescriptorType.BOS, 0, 5), "GET_DESCRIPTOR(BOS, 5)")
        total = int.from_bytes(header.data[2:4], "little")
        bos = self.require(await self.host.get_descriptor(DescriptorType.BOS, 0, total), "GET_DESCRIPTOR(BOS)")
        extensions = [d for d in iter_descriptors(bos.data) if isinstance(d, DeviceCapabilityDescriptor)
                      and d.capability_type == DeviceCapabilityType.USB20_EXTENSION]
        if not self.check(bool(extensions), "no USB 2.0 Extension capability in the BOS (step 6)"):
            return
        attributes = int.from_bytes(extensions[0].raw[3:7], "little")
        self.check(bool(attributes & BESL_SUPPORTED), "the BESL and Alternate HIRD Supported bit is not set (step 7)")
        self.info("L1 suspend/resume (steps 8-10) not performed: the host model has no LPM-capable port")


@pyuvm.test(timeout_time=SIM_TIMEOUT_MS, timeout_unit="ms")
class TD_9_21_LpmL1SuspendResumeTest(Ch9BaseTest):
    sequence = LpmSeq
