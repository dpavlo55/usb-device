"""The USB device verification environment.

::

    clk_sys / clk_usb agents ──> ResetSignal(sys/usb) ──> every reset-aware component

    USB host agent ── monitor.ap ──────────> transfer monitor ── transaction_ap ──> scoreboard
         │               │                         │                          └─> protocol checker
         │               └─> protocol checker      └── control_ap ──> ch9 tracker ──> coverage
         └ line_event_ap ──> transfer monitor, scoreboard, ch9 tracker, coverage

    device component (software + HAL) ── intent_ap ──> scoreboard
      └─ RTL: RegisterHal ─ RAL ─ AxilRegAdapter ─ AXI4-Lite agent ── monitor ──> predictor
      └─ reference: ReferenceHal (behavioral core on the bus pins)
"""
from __future__ import annotations

from pyuvm import ConfigDB, uvm_env, uvm_sequencer

from ..axil.axil_agent import AxilAgent, AxilConfig
from ..axil.axil_reg import AxilRegAdapter, AxilRegPredictor
from ..clkrst.clkrst_agent import ClkRstAgent, ClkRstConfig
from ..common.reset import ResetSignal, reset_key
from ..device.device_component import DeviceConfig, DutKind, UsbDeviceComponent
from ..device.reference_hal import ReferencePins
from ..ral import build_register_model
from ..usb.host_agent import UsbHostAgent, UsbHostConfig
from ..usb.transfers import UsbTransferMonitor
from .ch9_tracker import Ch9StateTracker
from .coverage import Ch9Coverage
from .env_config import UsbEnvConfig
from .protocol_checker import UsbProtocolChecker
from .scoreboard import UsbScoreboard


class UsbVirtualSequencer(uvm_sequencer):
    """Handles for virtual sequences: the sequencers they drive and the device they talk to."""

    def __init__(self, name, parent):
        super().__init__(name, parent)
        self.host = None
        self.sys_clock = None
        self.usb_clock = None
        self.device: UsbDeviceComponent | None = None
        self.cfg: UsbEnvConfig | None = None


class UsbEnv(uvm_env):
    def build_phase(self):
        self.cfg: UsbEnvConfig = ConfigDB().get(self, "", "env_cfg")
        cfg, dut = self.cfg, self.cfg.dut
        for domain in ("sys", "usb"):
            ConfigDB().set(self, "*", reset_key(domain), ResetSignal(domain))
        ConfigDB().set(self, "*", "compliance", cfg.compliance)
        ConfigDB().set(self, "*", "timing", cfg.timing)
        ConfigDB().set(self, "*", "coverage_dir", str(cfg.output_dir))
        ConfigDB().set(self, "*", "test_name", cfg.test_name)

        ConfigDB().set(self, "sys_clock", "cfg", ClkRstConfig("sys", dut.clk_sys, dut.rst_sys_n, cfg.sys_period_ps))
        ConfigDB().set(self, "usb_clock", "cfg", ClkRstConfig("usb", dut.clk_usb, dut.rst_usb_n, cfg.usb_period_ps))
        self.sys_clock = ClkRstAgent("sys_clock", self)
        self.usb_clock = ClkRstAgent("usb_clock", self)

        ConfigDB().set(self, "host", "cfg", UsbHostConfig(dut.usb_dp, dut.usb_dn, dut.usb_dp_host,
                                                          dut.usb_dn_host, cfg.timing, cfg.seed))
        self.host = UsbHostAgent("host", self)
        self.transfers = UsbTransferMonitor("transfers", self)

        reference_pins = None
        if cfg.kind is DutKind.RTL:
            ConfigDB().set(self, "axil", "cfg", AxilConfig(dut, "axil", dut.clk_sys, dut.rst_sys_n))
            self.axil = AxilAgent("axil", self)
            self.register_model = build_register_model()
            self.register_adapter = AxilRegAdapter()
            self.register_predictor = AxilRegPredictor("register_predictor", self)
            ConfigDB().set(self, "*", "register_model", self.register_model)
        else:
            reference_pins = ReferencePins(dut.usb_dp, dut.usb_dn, dut.usb_dp_dev, dut.usb_dn_dev, dut.usb_pu_dev)
        ConfigDB().set(self, "device", "device_cfg", DeviceConfig(
            cfg.descriptors, cfg.kind, cfg.seed, reference_pins=reference_pins,
            irq=cfg.dut.irq if cfg.kind is DutKind.RTL else None,
            software_faults=cfg.software_faults, hardware_faults=cfg.hardware_faults))
        self.device = UsbDeviceComponent("device", self)

        self.scoreboard = UsbScoreboard("scoreboard", self)
        self.protocol = UsbProtocolChecker("protocol", self)
        self.tracker = Ch9StateTracker("tracker", self)
        self.coverage = Ch9Coverage("coverage", self)
        self.vseqr = UsbVirtualSequencer("vseqr", self)

    def connect_phase(self):
        monitor = self.host.monitor
        monitor.ap.connect(self.transfers.analysis_export)
        monitor.ap.connect(self.protocol.packet_export)
        for export in (self.transfers.line_event_export, self.scoreboard.line_event_export,
                       self.tracker.line_event_export, self.coverage.line_event_export):
            monitor.line_event_ap.connect(export)
        self.transfers.transaction_ap.connect(self.scoreboard.transaction_export)
        self.transfers.transaction_ap.connect(self.protocol.transaction_export)
        self.transfers.control_ap.connect(self.tracker.control_export)
        self.tracker.annotated_ap.connect(self.coverage.control_export)
        self.device.intent_ap.connect(self.scoreboard.intent_export)
        self.coverage.tracker = self.tracker

        if self.cfg.kind is DutKind.RTL:
            register_map = self.register_model.get_default_map()
            register_map.set_sequencer(self.axil.sequencer, self.register_adapter)
            register_map.set_auto_predict(False)
            self.register_predictor.map = register_map
            self.axil.monitor.ap.connect(self.register_predictor.analysis_export)

        self.vseqr.host = self.host.sequencer
        self.vseqr.sys_clock = self.sys_clock.sequencer
        self.vseqr.usb_clock = self.usb_clock.sequencer
        self.vseqr.device = self.device
        self.vseqr.cfg = self.cfg
