"""The base test of the Chapter 9 suite.

A test is a configuration of the environment plus one procedure (a virtual sequence).
The base test builds the environment from ``DUT``/``SEED``/``TIMING``, powers the design
up through the clock/reset agents (both domains in parallel), runs the procedure, and
fails in its report phase on any compliance violation.
"""
from __future__ import annotations

import cocotb
from cocotb.triggers import gather
from pyuvm import ConfigDB, uvm_test

from usbtb.ch9.descriptors import DescriptorSet
from usbtb.ch9.device import SoftwareFault
from usbtb.clkrst.clkrst_agent import PowerOnResetSeq
from usbtb.device.reference_hal import HardwareFault
from usbtb.env.env_config import UsbEnvConfig
from usbtb.env.usb_env import UsbEnv
from usbtb.seq.ch9_base import Ch9Sequence
from usbtb.seq.host import UsbHost
from usbtb.usb.timing import UsbTiming
from usbtb.usb.wire import US_FS

DRAIN_FS = 200 * US_FS
"""Bus idle after the procedure, so the last transfers reach the scoreboard."""

SIM_TIMEOUT_MS = {"fast": 250, "spec": 4_000}[UsbTiming.from_environment().name]
"""Fast-timing tests need at most ~65 ms of sim time; a hang must fail in seconds, not
the ~45 wall-clock minutes 4 s of sim time takes."""


class Ch9BaseTest(uvm_test):
    sequence: type[Ch9Sequence]
    software_faults: frozenset[SoftwareFault] = frozenset()
    hardware_faults: frozenset[HardwareFault] = frozenset()

    @staticmethod
    def profile_filter(descriptors: DescriptorSet) -> bool:
        """A test-level constraint on the random device profile (default: any device)."""
        return True

    def build_phase(self):
        self.cfg = UsbEnvConfig.from_environment(cocotb.top, type(self).__name__, self.profile_filter,
                                                 software_faults=self.software_faults,
                                                 hardware_faults=self.hardware_faults)
        ConfigDB().set(self, "env", "env_cfg", self.cfg)
        self.env = UsbEnv("env", self)

    def end_of_elaboration_phase(self):
        self.logger.info("%s\n%s", type(self).__name__, self.cfg.summary())

    async def run_phase(self):
        self.raise_objection()
        self.env.coverage.sample_profile(self.cfg.descriptors)
        await self.power_on()
        await self.sequence().start(self.env.vseqr)
        await UsbHost(self.env.host.sequencer, self.cfg.timing).idle(DRAIN_FS)
        self.drop_objection()

    async def power_on(self) -> None:
        dut = self.cfg.dut
        dut.usb_vbus.value = 1
        await gather(PowerOnResetSeq("sys_por").start(self.env.sys_clock.sequencer),
                     PowerOnResetSeq("usb_por").start(self.env.usb_clock.sequencer))

    def report_phase(self):
        log = self.cfg.compliance
        self.logger.info("%s: %s (SEED=%d)", type(self).__name__, log.summary(), self.cfg.seed)
        self.check_result(log)

    def check_result(self, log) -> None:
        assert log.passed, (f"{len(log.violations)} compliance violation(s), replay with SEED={self.cfg.seed}:\n"
                            + "\n".join(str(v) for v in log.violations))
