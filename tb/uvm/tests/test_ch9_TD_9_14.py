"""TD 9.14: Suspend/Resume Test (USB 2.0 CV specification, section 3.1.14).

From the Address state: suspend the bus, resume it, and the device must answer
GET_DESCRIPTOR(DEVICE) (otherwise it is re-enumerated, a failure). USBCV waits about
200 ms in each state; here the bus stays idle long enough for the device's 3 ms suspend
timer (``TIMING`` sets the host's resume and recovery times).

Beyond USBCV, which only sees the bus: the device software must have been told about
both transitions, a suspend event during the idle bus and a resume event after the
host's resume signaling (a device that ignores suspend would pass USBCV).
"""
import pyuvm

from ch9_base_test import SIM_TIMEOUT_MS, Ch9BaseTest
from usbtb.ch9.constants import DeviceState
from usbtb.seq.ch9_base import Ch9Sequence


class SuspendResumeSeq(Ch9Sequence):
    """Suspend, resume, and the device still answers."""

    td = "TD 9.14"

    async def procedure(self):
        await self.enter_state(DeviceState.ADDRESS)
        seen = len(self.device.bus_events())
        await self.host.suspend()
        events = self.device.bus_events()[seen:]
        self.check(events == ["suspend"], f"bus events during the idle bus: {events}, expected one suspend")
        await self.host.resume()
        events = self.device.bus_events()[seen:]
        self.check(events == ["suspend", "resume"], f"bus events after resume: {events}, expected suspend, resume")
        await self.ensure_responding(DeviceState.ADDRESS, "suspend and resume")


@pyuvm.test(timeout_time=SIM_TIMEOUT_MS, timeout_unit="ms")
class TD_9_14_SuspendResumeTest(Ch9BaseTest):
    sequence = SuspendResumeSeq
