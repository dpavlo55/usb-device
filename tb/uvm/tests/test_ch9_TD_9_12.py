"""TD 9.12: Remote Wakeup Test (USB 2.0 CV specification, section 3.1.12).

From the Address state, find the configurations with bmAttributes D5 (remote wakeup).
For each, twice, with remote wakeup enabled and then disabled:

* SET_CONFIGURATION / GET_CONFIGURATION,
* SET_FEATURE(DEVICE_REMOTE_WAKEUP), GET_STATUS reports it; for the disabled run,
  CLEAR_FEATURE(DEVICE_REMOTE_WAKEUP), GET_STATUS no longer reports it,
* suspend the bus, then the "user" asks the device application to wake the host,
* enabled: the device must signal resume; disabled: it must not (the host then resumes).

The test constrains the random device to have a remote-wakeup capable configuration.
"""
import cocotb
import pyuvm

from ch9_base_test import SIM_TIMEOUT_MS, Ch9BaseTest
from usbtb.ch9.constants import ConfigAttributes, DeviceState, DeviceStatus, FeatureSelector
from usbtb.ch9.descriptors import ConfigurationDescriptor
from usbtb.ch9.profile import has_remote_wakeup
from usbtb.seq.ch9_base import Ch9Sequence


class RemoteWakeupSeq(Ch9Sequence):
    """DEVICE_REMOTE_WAKEUP set and cleared, and the wakeup it allows or forbids."""

    td = "TD 9.12"

    async def procedure(self):
        enumeration = await self.enter_state(DeviceState.ADDRESS)
        capable = []
        for index in range(len(enumeration.configurations)):
            header = self.require(await self.host.get_configuration_descriptor(index, 9),
                                  f"GET_DESCRIPTOR(CONFIGURATION {index}, 9)")
            descriptor = ConfigurationDescriptor.from_bytes(header.data)
            self.check(descriptor.length == 9, f"configuration {index}: bLength {descriptor.length} (step 3)")
            if descriptor.attributes & ConfigAttributes.REMOTE_WAKEUP:
                capable.append(descriptor.configuration_value)
        if not capable:
            self.info("no configuration supports remote wakeup: nothing to test (step 5)")
            return
        for value in capable:
            for enabled in (True, False):
                await self.remote_wakeup_test(value, enabled)

    async def remote_wakeup_test(self, value: int, enabled: bool) -> None:
        host, mode = self.host, "enabled" if enabled else "disabled"
        self.info(f"--- remote wakeup {mode}, configuration {value}")
        await self.enter_state(DeviceState.ADDRESS)
        await self.set_and_verify_configuration(value)  # steps 7-8
        self.require(await host.set_feature(FeatureSelector.DEVICE_REMOTE_WAKEUP),
                     "SET_FEATURE(DEVICE_REMOTE_WAKEUP)")
        await self.expect_wakeup_status(True)  # step 10
        if not enabled:  # step 11
            self.require(await host.clear_feature(FeatureSelector.DEVICE_REMOTE_WAKEUP),
                         "CLEAR_FEATURE(DEVICE_REMOTE_WAKEUP)")
            await self.expect_wakeup_status(False)
        await host.suspend()  # steps 12-14
        wakeup_request = cocotb.start_soon(self.device.request_remote_wakeup())  # step 15
        woke = await host.wait_for_remote_wakeup()  # step 16
        await wakeup_request
        if enabled:
            self.check(woke, "the device did not signal remote wakeup although it is enabled")
        else:
            self.check(not woke, "the device signaled remote wakeup although it is disabled")
            await host.resume()
        self.require(await host.get_device_descriptor(18), "GET_DESCRIPTOR(DEVICE) after resume")

    async def expect_wakeup_status(self, expected: bool) -> None:
        result, status = await self.host.get_status()
        self.require(result, "GET_STATUS(device)")
        reported = bool(status & DeviceStatus.REMOTE_WAKEUP)
        self.check(reported == expected, f"GET_STATUS reports remote wakeup {reported}, expected {expected}")


@pyuvm.test(timeout_time=SIM_TIMEOUT_MS, timeout_unit="ms")
class TD_9_12_RemoteWakeupTest(Ch9BaseTest):
    sequence = RemoteWakeupSeq
    profile_filter = staticmethod(has_remote_wakeup)
