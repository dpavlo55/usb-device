"""The HAL writes endpoint kinds in the RDL's encoding, not the descriptor's."""
import re
from pathlib import Path

from usbtb.ch9.constants import TransferType
from usbtb.device.hal import RDL_ENDPOINT_KIND

RDL = Path(__file__).resolve().parents[3] / "rdl" / "usb_device.rdl"


def _rdl_enum(name: str) -> dict[str, int]:
    body = re.search(rf"enum {name}\s*{{(.*?)\n\s*}};", RDL.read_text(), re.S).group(1)
    return {m.group(1): int(m.group(2), 0) for m in re.finditer(r"(\w+)\s*=\s*(0x[0-9a-fA-F]+|\d+)", body)}


def test_endpoint_kind_matches_the_rdl():
    rdl = _rdl_enum("endpoint_kind_e")
    assert {kind.name: value for kind, value in RDL_ENDPOINT_KIND.items()} == rdl
    assert set(RDL_ENDPOINT_KIND) == set(TransferType)
