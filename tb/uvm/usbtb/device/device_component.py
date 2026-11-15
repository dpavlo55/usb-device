"""The device software as a reset-aware UVM component.

It owns the device software and its HAL: the RAL-based HAL for the RTL, or the reference
model. What the software asks of the hardware is published on ``intent_ap`` for the
scoreboard. After every reset, a fresh software instance starts from power-on.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field

from pyuvm import ConfigDB, uvm_analysis_port, uvm_component

from ..ch9.descriptors import DescriptorSet
from ..ch9.device import SoftwareFault
from ..common.reset import ResetAware
from ..common.seeding import rng
from .hal import DeviceHal
from .reference_hal import HardwareFault, ReferenceHal, ReferencePins
from .register_hal import RegisterHal
from .software import DeviceSoftware


class DutKind(enum.Enum):
    RTL = "rtl"
    REFERENCE = "reference"


@dataclass
class DeviceConfig:
    descriptors: DescriptorSet
    kind: DutKind
    seed: int
    max_latency_ns: int = 3_000
    poll_interval_ns: int = 500
    reference_pins: ReferencePins | None = None
    irq: object | None = None
    software_faults: frozenset[SoftwareFault] = field(default_factory=frozenset)
    hardware_faults: frozenset[HardwareFault] = field(default_factory=frozenset)


class UsbDeviceComponent(ResetAware, uvm_component):
    def build_phase(self):
        super().build_phase()
        self.cfg: DeviceConfig = ConfigDB().get(self, "", "device_cfg")
        self.intent_ap = uvm_analysis_port("intent_ap", self)
        self.software: DeviceSoftware | None = None
        self._hal: DeviceHal | None = None

    def _make_hal(self) -> DeviceHal:
        if self.cfg.kind is DutKind.RTL:
            register_model = ConfigDB().get(self, "", "register_model")
            return RegisterHal(register_model, self.logger, self.cfg.poll_interval_ns, self.cfg.irq)
        return ReferenceHal(self.cfg.reference_pins, self.logger, rng(self.cfg.seed, "reference"),
                            self.cfg.hardware_faults)

    async def run_out_of_reset(self):
        self._hal = self._make_hal()
        self.software = DeviceSoftware(
            self._hal, self.cfg.descriptors, publish=self.intent_ap.write, logger=self.logger,
            rng=rng(self.cfg.seed, f"software{self.reset.count}"), max_latency_ns=self.cfg.max_latency_ns,
            faults=self.cfg.software_faults)
        await self.software.run()

    def on_reset(self):
        if self.software is not None:
            self.software.stop()
        if self._hal is not None:
            self._hal.stop()
        self.software = None
        self._hal = None

    def bus_events(self) -> list[str]:
        """Suspend/resume events the device software has received since its last start."""
        return list(self.software.bus_events) if self.software is not None else []

    async def request_remote_wakeup(self) -> bool:
        """The 'user action' of TD 9.12: ask the device application to wake the host."""
        return self.software is not None and await self.software.request_remote_wakeup()
