"""The register model, generated from ``rdl/usb_device.rdl``.

``usb_device_ral.py`` is produced by ``make -C rdl pyuvm`` (``run.py`` regenerates it before
every simulation), so the RDL stays the single source of truth for addresses and fields.
"""
from __future__ import annotations

from pyuvm import uvm_mem

from .usb_device_ral import usb_device, usb_device__usb_ram


def build_register_model(name: str = "usb_device") -> usb_device:
    model = usb_device(name)
    model.build()
    model.lock_model()
    return model


def usb_ram(model: usb_device) -> uvm_mem:
    """The shared endpoint RAM (the generator names its memory privately)."""
    return model.usb_ram._mem


def ram_bytes() -> int:
    """Bytes of the shared RAM, as the RDL's ``RAM_ADDR_WIDTH`` generated them."""
    return usb_device__usb_ram()._mem.get_size()
