"""TD 9.16: Enumeration Test (USB 2.0 CV specification, section 3.1.16).

From the Address state, the device is enumerated repeatedly with the "Enumerate Device
Routine", each time at a different random address. Randomized on top of USBCV:

* the number of enumerations;
* how each one starts: a USB bus reset, or a full reset of the device (both clock
  domains), after which the device must re-attach at full speed;
* a probe at the previous address after re-enumeration, which must stay unanswered.
"""
import pyuvm
from cocotb.triggers import gather

from ch9_base_test import SIM_TIMEOUT_MS, Ch9BaseTest
from usbtb.ch9.constants import DeviceState
from usbtb.clkrst.clkrst_agent import ResetPulseSeq
from usbtb.seq.ch9_base import ATTACH_TIMEOUT_FS, Ch9Aborted, Ch9Sequence
from usbtb.usb.wire import Pid

ENUMERATIONS = (3, 6)


class EnumerationSeq(Ch9Sequence):
    """Repeated enumeration at changing addresses."""

    td = "TD 9.16"

    async def procedure(self):
        await self.enter_state(DeviceState.ADDRESS)
        count = self.rng.randint(*ENUMERATIONS)
        self.info(f"{count} enumerations")
        for iteration in range(count):
            previous = self.host.address
            if self.rng.random() < 0.3:
                self.info(f"enumeration {iteration + 1}: after a device reset")
                await self.reset_device()
            else:
                self.info(f"enumeration {iteration + 1}: after a bus reset")
            await self.host.bus_reset()
            await self.enumerate()
            await self.probe_old_address(previous)

    async def reset_device(self) -> None:
        """Assert both resets mid-test: every reset-aware component restarts."""
        vseqr = self.sequencer
        await gather(ResetPulseSeq("sys_reset").start(vseqr.sys_clock),
                     ResetPulseSeq("usb_reset").start(vseqr.usb_clock))
        if not await self.host.wait_for_attach(ATTACH_TIMEOUT_FS):
            raise Ch9Aborted("the device did not re-attach at full speed after reset")

    async def probe_old_address(self, previous: int) -> None:
        if previous in (0, self.host.address):
            return
        txn = await self.host.transaction(Pid.IN, 0, address=previous)
        self.check(txn.timed_out, f"an IN token to the old address {previous} was answered with "
                                  f"{txn.response.name if txn.response else 'a corrupt packet'}")


@pyuvm.test(timeout_time=SIM_TIMEOUT_MS, timeout_unit="ms")
class TD_9_16_EnumerationTest(Ch9BaseTest):
    sequence = EnumerationSeq
