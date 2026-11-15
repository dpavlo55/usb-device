"""TLM helpers."""
from __future__ import annotations

from collections.abc import Callable

from pyuvm import uvm_component, uvm_subscriber


def analysis_imp(name: str, parent: uvm_component, write: Callable[[object], None]):
    """An analysis export that calls ``write``: lets one component subscribe to several ports
    (SystemVerilog's ``uvm_analysis_imp_decl`` pattern)."""
    return uvm_subscriber.uvm_AnalysisImp(name, parent, write)
