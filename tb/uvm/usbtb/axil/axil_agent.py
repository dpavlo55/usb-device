"""AXI4-Lite UVC: sequence item, driver, monitor and agent.

The protocol work is done by cocotbext-axi: the driver delegates each item to an
``AxiLiteMaster`` and the monitor is built from its five channel monitors. This file adds
the UVM structure around them: items on a sequencer, observed transactions on an
analysis port, and reset awareness.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass

import cocotb
from cocotb.handle import SimHandleBase
from cocotb.queue import Queue
from cocotb.utils import get_sim_time
from cocotbext.axi import AxiLiteBus, AxiLiteMaster
from cocotbext.axi.axil_channels import (
    AxiLiteARBus,
    AxiLiteARMonitor,
    AxiLiteAWBus,
    AxiLiteAWMonitor,
    AxiLiteBBus,
    AxiLiteBMonitor,
    AxiLiteRBus,
    AxiLiteRMonitor,
    AxiLiteWBus,
    AxiLiteWMonitor,
)
from pyuvm import (
    ConfigDB,
    uvm_active_passive_enum,
    uvm_agent,
    uvm_analysis_port,
    uvm_driver,
    uvm_monitor,
    uvm_sequence_item,
    uvm_sequencer,
)

from ..common.reset import ResetAware

OKAY = 0


def to_int(value) -> tuple[int, bool]:
    """A bus value as an integer, and whether it was fully known. Unknown bits (for example
    unwritten RAM lanes) read as 0."""
    if value.is_resolvable:
        return int(value), True
    return int("".join(c if c in "01" else "0" for c in str(value)), 2), False


class AxilOp(enum.Enum):
    READ = "read"
    WRITE = "write"


@dataclass
class AxilConfig:
    dut: SimHandleBase
    prefix: str
    clock: SimHandleBase
    reset: SimHandleBase
    reset_active_low: bool = True
    is_active: uvm_active_passive_enum = uvm_active_passive_enum.UVM_ACTIVE

    def bus_kwargs(self) -> dict:
        return {"reset": self.reset, "reset_active_level": not self.reset_active_low}


class AxilItem(uvm_sequence_item):
    """One access: ``size`` bytes at ``address``, little-endian ``data``."""

    def __init__(self, name: str = "axil_item", op: AxilOp = AxilOp.READ, address: int = 0,
                 data: int = 0, size: int = 4):
        super().__init__(name)
        self.op = op
        self.address = address
        self.data = data
        self.size = size
        self.resp: int | None = None
        self.created_fs = round(get_sim_time(unit="fs"))
        self.known = True
        """False when an observed value had X/U/Z bits."""

    @property
    def ok(self) -> bool:
        return self.resp == OKAY

    def __str__(self) -> str:
        resp = "" if self.resp is None else f" resp={self.resp}"
        return f"AXI-Lite {self.op.value} 0x{self.address:03X} [{self.size}] = 0x{self.data:0{2 * self.size}X}{resp}"


class AxilDriver(ResetAware, uvm_driver):
    """Executes items with cocotbext-axi.

    An item cut short by reset completes with no response (``resp`` is ``None``), and so
    does any item that was requested before the last reset: a reset discards the accesses
    that were waiting for the bus.
    """

    def build_phase(self):
        super().build_phase()
        cfg: AxilConfig = ConfigDB().get(self, "", "cfg")
        bus = AxiLiteBus.from_prefix(cfg.dut, cfg.prefix)
        self.master = AxiLiteMaster(bus, cfg.clock, **cfg.bus_kwargs())
        self._current: AxilItem | None = None

    async def run_out_of_reset(self):
        while True:
            self._current = await self.seq_item_port.get_next_item()
            if self._current.created_fs >= self.reset.asserted_at_fs:
                await self._execute(self._current)
            self._current = None
            self.seq_item_port.item_done()

    async def _execute(self, item: AxilItem) -> None:
        if item.op is AxilOp.WRITE:
            response = await self.master.write(item.address, item.data.to_bytes(item.size, "little"))
        else:
            response = await self.master.read(item.address, item.size)
            item.data = int.from_bytes(response.data, "little")
        item.resp = int(response.resp)

    def on_reset(self):
        if self._current is not None:
            self._current.resp = None
            self._current = None
            self.seq_item_port.item_done()


class AxilMonitor(ResetAware, uvm_monitor):
    """Pairs the address, data and response channels (in order: AXI4-Lite has no IDs)."""

    def build_phase(self):
        super().build_phase()
        cfg: AxilConfig = ConfigDB().get(self, "", "cfg")
        kwargs = cfg.bus_kwargs()
        self.ap = uvm_analysis_port("ap", self)
        self._aw = AxiLiteAWMonitor(AxiLiteAWBus.from_prefix(cfg.dut, cfg.prefix), cfg.clock, **kwargs)
        self._w = AxiLiteWMonitor(AxiLiteWBus.from_prefix(cfg.dut, cfg.prefix), cfg.clock, **kwargs)
        self._b = AxiLiteBMonitor(AxiLiteBBus.from_prefix(cfg.dut, cfg.prefix), cfg.clock, **kwargs)
        self._ar = AxiLiteARMonitor(AxiLiteARBus.from_prefix(cfg.dut, cfg.prefix), cfg.clock, **kwargs)
        self._r = AxiLiteRMonitor(AxiLiteRBus.from_prefix(cfg.dut, cfg.prefix), cfg.clock, **kwargs)
        self.byte_lanes = len(self._w.bus.wdata) // 8

    async def run_out_of_reset(self):
        reads = cocotb.start_soon(self._reads())
        try:
            await self._writes()
        finally:
            reads.cancel()

    async def _writes(self) -> None:
        while True:
            aw = await self._aw.recv()
            w = await self._w.recv()
            b = await self._b.recv()
            strobe = int(w.wstrb) if hasattr(w, "wstrb") else (1 << self.byte_lanes) - 1
            lanes = [lane for lane in range(self.byte_lanes) if strobe >> lane & 1]
            first = lanes[0] if lanes else 0
            data, known = to_int(w.wdata)
            item = AxilItem("observed_write", AxilOp.WRITE, int(aw.awaddr) + first,
                            (data >> (8 * first)) & ((1 << (8 * len(lanes))) - 1), len(lanes) or 1)
            item.resp, item.known = int(b.bresp), known
            self.ap.write(item)

    async def _reads(self) -> None:
        while True:
            ar = await self._ar.recv()
            r = await self._r.recv()
            data, known = to_int(r.rdata)
            item = AxilItem("observed_read", AxilOp.READ, int(ar.araddr), data, self.byte_lanes)
            item.resp, item.known = int(r.rresp), known
            self.ap.write(item)

    def on_reset(self):
        for channel in (self._aw, self._w, self._b, self._ar, self._r):
            channel.queue = Queue()


class AxilAgent(uvm_agent):
    def build_phase(self):
        self.cfg: AxilConfig = ConfigDB().get(self, "", "cfg")
        ConfigDB().set(self, "*", "cfg", self.cfg)
        self.monitor = AxilMonitor("monitor", self)
        if self.cfg.is_active == uvm_active_passive_enum.UVM_ACTIVE:
            self.sequencer = uvm_sequencer("sequencer", self)
            self.driver = AxilDriver("driver", self)

    def connect_phase(self):
        if self.cfg.is_active == uvm_active_passive_enum.UVM_ACTIVE:
            self.driver.seq_item_port.connect(self.sequencer.seq_item_export)
