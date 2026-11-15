"""Everything that varies between runs, in one object read from the environment."""
from __future__ import annotations

import itertools
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from cocotb.handle import SimHandleBase

from ..ch9.descriptors import DescriptorSet
from ..ch9.device import SoftwareFault
from ..ch9.profile import DeviceProfile, describe
from ..common.compliance import ComplianceLog
from ..common.seeding import resolve_seed
from ..device.device_component import DutKind
from ..device.reference_hal import HardwareFault
from ..usb.timing import UsbTiming

ProfileFilter = Callable[[DescriptorSet], bool]
PROFILE_SEARCH_LIMIT = 1000

SYS_CLOCK_PERIOD_PS = 20_000
"""clk_sys: 50 MHz."""
USB_CLOCK_PERIOD_PS = 20_833
"""clk_usb: 48 MHz, four samples per full-speed bit."""


@dataclass
class UsbEnvConfig:
    dut: SimHandleBase
    kind: DutKind
    seed: int
    timing: UsbTiming
    descriptors: DescriptorSet
    profile_seed: int
    test_name: str
    output_dir: Path
    compliance: ComplianceLog = field(default_factory=ComplianceLog)
    sys_period_ps: int = SYS_CLOCK_PERIOD_PS
    usb_period_ps: int = USB_CLOCK_PERIOD_PS
    software_faults: frozenset[SoftwareFault] = frozenset()
    hardware_faults: frozenset[HardwareFault] = frozenset()

    @classmethod
    def from_environment(cls, dut: SimHandleBase, test_name: str, profile_filter: ProfileFilter | None = None,
                         **overrides) -> UsbEnvConfig:
        """``DUT`` (rtl or reference), ``SEED`` and ``TIMING`` select the run.

        ``profile_filter`` is a test-level constraint on the random device: profiles are
        drawn from ``SEED``, ``SEED + 1``, ... until one satisfies it (deterministic per seed).
        """
        seed = resolve_seed()
        kind = DutKind(os.environ.get("DUT", "rtl").lower())
        output_dir = Path(os.environ.get("UVM_OUTPUT_DIR", "."))
        profile_seed, descriptors = cls._draw_profile(seed, profile_filter or (lambda _: True))
        return cls(dut=dut, kind=kind, seed=seed, timing=UsbTiming.from_environment(),
                   descriptors=descriptors, profile_seed=profile_seed, test_name=test_name,
                   output_dir=output_dir, **overrides)

    @staticmethod
    def _draw_profile(seed: int, accept: ProfileFilter) -> tuple[int, DescriptorSet]:
        for candidate in itertools.islice(itertools.count(seed), PROFILE_SEARCH_LIMIT):
            descriptors = DeviceProfile(candidate).descriptor_set()
            if accept(descriptors):
                return candidate, descriptors
        raise ValueError(f"no device profile in {PROFILE_SEARCH_LIMIT} seeds satisfies the test's filter")

    def summary(self) -> str:
        return (f"DUT={self.kind.value} SEED={self.seed} (profile seed {self.profile_seed}) "
                f"TIMING={self.timing.name}\ndevice profile:\n{describe(self.descriptors)}")
