"""Self-checks of the testbench: every checker must catch the bug it exists for.

Each test switches on one deliberate bug in the reference device (``HardwareFault``) or in
the device software (``SoftwareFault``), runs a Chapter 9 procedure, and passes only if
the expected violation is reported. Run with ``run.py --dut reference --selfcheck``.
"""
import pyuvm

from ch9_base_test import SIM_TIMEOUT_MS, Ch9BaseTest
from test_ch9_TD_9_1 import DeviceDescriptorSeq
from test_ch9_TD_9_10 import BadDescriptorSeq
from test_ch9_TD_9_12 import RemoteWakeupSeq
from test_ch9_TD_9_16 import EnumerationSeq
from usbtb.ch9.device import SoftwareFault
from usbtb.ch9.profile import has_remote_wakeup
from usbtb.common.compliance import ComplianceLog
from usbtb.device.reference_hal import HardwareFault


class SelfCheckTest(Ch9BaseTest):
    expected_violation: str

    def build_phase(self):
        super().build_phase()
        self.cfg.compliance.expecting_violations = True

    def check_result(self, log: ComplianceLog) -> None:
        caught = log.matching(self.expected_violation)
        assert caught, (f"the injected bug was not detected: no violation mentions {self.expected_violation!r} "
                        f"(violations: {[str(v) for v in log.violations] or 'none'})")
        self.logger.info("injected bug detected as expected: %s", caught[0])


@pyuvm.test(timeout_time=SIM_TIMEOUT_MS, timeout_unit="ms")
class SelfCheck_DeviceDescriptorContent(SelfCheckTest):
    """USBCV content check (TD 9.1) on a device descriptor with bLength 17."""

    sequence = DeviceDescriptorSeq
    software_faults = frozenset({SoftwareFault.BAD_DEVICE_DESCRIPTOR_LENGTH})
    expected_violation = "bLength is 17"


@pyuvm.test(timeout_time=SIM_TIMEOUT_MS, timeout_unit="ms")
class SelfCheck_RequestErrorNotStalled(SelfCheckTest):
    """TD 9.10 on a device that answers undefined descriptor types instead of stalling."""

    sequence = BadDescriptorSeq
    software_faults = frozenset({SoftwareFault.NO_STALL_ON_BAD_REQUEST})
    expected_violation = "must be answered with STALL"


@pyuvm.test(timeout_time=SIM_TIMEOUT_MS, timeout_unit="ms")
class SelfCheck_WakeupWhileDisabled(SelfCheckTest):
    """TD 9.12 on a device that wakes the host although remote wakeup is disabled."""

    sequence = RemoteWakeupSeq
    software_faults = frozenset({SoftwareFault.WAKEUP_WHEN_DISABLED})
    profile_filter = staticmethod(has_remote_wakeup)
    expected_violation = "although it is disabled"


@pyuvm.test(timeout_time=SIM_TIMEOUT_MS, timeout_unit="ms")
class SelfCheck_DataToggle(SelfCheckTest):
    """Protocol checker: endpoint 0 IN data with the wrong DATA PID."""

    sequence = DeviceDescriptorSeq
    hardware_faults = frozenset({HardwareFault.WRONG_DATA_TOGGLE})
    expected_violation = "expected DATA1"


@pyuvm.test(timeout_time=SIM_TIMEOUT_MS, timeout_unit="ms")
class SelfCheck_InDataIntegrity(SelfCheckTest):
    """Scoreboard: IN data on the wire differs from what the software armed."""

    sequence = DeviceDescriptorSeq
    hardware_faults = frozenset({HardwareFault.CORRUPT_IN_DATA})
    expected_violation = "software armed"


@pyuvm.test(timeout_time=SIM_TIMEOUT_MS, timeout_unit="ms")
class SelfCheck_Turnaround(SelfCheckTest):
    """Protocol checker: the device answers too late."""

    sequence = DeviceDescriptorSeq
    hardware_faults = frozenset({HardwareFault.SLOW_RESPONSE})
    expected_violation = "bit times"


@pyuvm.test(timeout_time=SIM_TIMEOUT_MS, timeout_unit="ms")
class SelfCheck_AddressFiltering(SelfCheckTest):
    """Scoreboard and TD 9.16: the device answers tokens for an address it does not have."""

    sequence = EnumerationSeq
    hardware_faults = frozenset({HardwareFault.RESPOND_TO_ANY_ADDRESS})
    expected_violation = "address"
