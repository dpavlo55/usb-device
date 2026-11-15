"""TD 9.7: BOS Descriptor Test (USB 2.0 CV specification, section 3.1.7).

In the Default, Address and Configured states: read bcdUSB, then GET_DESCRIPTOR(BOS, 5).
A device with bcdUSB 0x0200 or lower must answer with a request error, and the test ends;
a newer device must return a 5-byte BOS header and a BOS set that passes steps 6 to 13.
"""
import pyuvm

from ch9_base_test import SIM_TIMEOUT_MS, Ch9BaseTest
from usbtb.ch9 import usbcv
from usbtb.ch9.constants import DescriptorType, DeviceState
from usbtb.ch9.descriptors import DeviceDescriptor
from usbtb.seq.ch9_base import Ch9Sequence


class BosDescriptorSeq(Ch9Sequence):
    """The BOS descriptor in every device state."""

    td = "TD 9.7"

    async def procedure(self):
        await self.for_each_state(self.check_bos, each_configuration=False)

    async def check_bos(self, state: DeviceState, _: int) -> None:
        device = self.require(await self.host.get_device_descriptor(18), "GET_DESCRIPTOR(DEVICE, 18)")
        bcd_usb = DeviceDescriptor.from_bytes(device.data).bcd_usb
        header = await self.host.get_descriptor(DescriptorType.BOS, 0, 5)
        if bcd_usb <= 0x0200:
            self.expect_request_error(header, f"GET_DESCRIPTOR(BOS) on a bcdUSB 0x{bcd_usb:04X} device (step 3)")
            return
        self.require(header, "GET_DESCRIPTOR(BOS, 5)")
        if not self.check(header.data[:1] == b"\x05", f"BOS bLength is {header.data[:1].hex()}, expected 05"):
            return
        total = int.from_bytes(header.data[2:4], "little")
        full = self.require(await self.host.get_descriptor(DescriptorType.BOS, 0, total), f"GET_DESCRIPTOR(BOS, {total})")
        self.check_findings(usbcv.check_bos(full.data, total), "BOS set (steps 6-13)")


@pyuvm.test(timeout_time=SIM_TIMEOUT_MS, timeout_unit="ms")
class TD_9_7_BosDescriptorTest(Ch9BaseTest):
    sequence = BosDescriptorSeq
