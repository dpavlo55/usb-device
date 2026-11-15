"""Register layer glue for the AXI4-Lite agent: the adapter and an explicit predictor.

The register model itself is generated from ``rdl/usb_device.rdl`` by peakrdl-pyuvm (see
:mod:`usbtb.ral`). Frontdoor accesses go through :class:`AxilRegAdapter` onto the AXI4-Lite
sequencer; :class:`AxilRegPredictor` updates the mirror from every *observed* bus access,
so the mirror stays right whoever started the access. (pyuvm's own ``uvm_reg_predictor``
is not implemented yet.)
"""
from __future__ import annotations

from pyuvm import (
    uvm_access_e,
    uvm_door_e,
    uvm_predict_e,
    uvm_reg_adapter,
    uvm_reg_bus_op,
    uvm_reg_map,
    uvm_status_e,
    uvm_subscriber,
)

from ..common.reset import ResetAware
from .axil_agent import AxilItem, AxilOp


class AxilRegAdapter(uvm_reg_adapter):
    """Translates register operations to AXI4-Lite items and back.

    Memory elements of the byte-wide ``usb_ram`` arrive as 8-bit operations; they become
    single-byte AXI writes, which ``AxiLiteMaster`` issues with the matching WSTRB lane.
    """

    def __init__(self, name: str = "axil_reg_adapter"):
        super().__init__(name)
        self.provides_responses = False

    def reg2bus(self, rw: uvm_reg_bus_op) -> AxilItem:
        op = AxilOp.WRITE if rw.kind == uvm_access_e.UVM_WRITE else AxilOp.READ
        return AxilItem(f"reg_{op.value}", op, int(rw.addr), int(rw.data) if op is AxilOp.WRITE else 0,
                        max(1, (rw.n_bits + 7) // 8))

    def bus2reg(self, bus_item: AxilItem, rw: uvm_reg_bus_op) -> None:
        rw.kind = uvm_access_e.UVM_WRITE if bus_item.op is AxilOp.WRITE else uvm_access_e.UVM_READ
        rw.addr = bus_item.address
        rw.data = bus_item.data
        rw.status = uvm_status_e.UVM_IS_OK if bus_item.ok else uvm_status_e.UVM_NOT_OK


class AxilRegPredictor(ResetAware, uvm_subscriber):
    """Updates the register mirror from the AXI4-Lite monitor's transactions; a reset of
    the register domain returns the whole mirror to its reset values."""

    def __init__(self, name, parent):
        super().__init__(name, parent)
        self.map: uvm_reg_map | None = None

    def on_reset(self) -> None:
        # Field by field: uvm_reg.reset() would also release the access lock of a register
        # whose frontdoor access is still in flight.
        for register in self.map.get_registers():
            for reg_field in register.get_fields():
                reg_field.reset()

    def write(self, item: AxilItem) -> None:
        if not item.ok or not item.known:
            return
        register = self.map.get_reg_by_offset(item.address & ~0x3, read=item.op is AxilOp.READ)
        if register is None:
            return
        kind = uvm_predict_e.UVM_PREDICT_WRITE if item.op is AxilOp.WRITE else uvm_predict_e.UVM_PREDICT_READ
        register.predict(item.data, kind=kind, path=uvm_door_e.UVM_FRONTDOOR, map=self.map)
