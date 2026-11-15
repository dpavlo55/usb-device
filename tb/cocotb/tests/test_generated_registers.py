"""The committed register block is what rdl/usb_device.rdl generates.

The RDL is the single source of the register map and of the shared RAM size
(``RAM_ADDR_WIDTH``); the RTL takes both from the generated package. Regenerating into
a temporary directory and comparing catches an RDL change without ``make -C rdl regs``.
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
GENERATED = ("usb_device_regs.vhd", "usb_device_regs_pkg.vhd", "reg_utils.vhd")


def test_committed_register_block_matches_the_rdl(tmp_path: Path) -> None:
    subprocess.run(["make", "-C", str(ROOT / "rdl"), "regs", f"PYTHON={Path(sys.executable).as_posix()}",
                    f"RTL_DIR={tmp_path.as_posix()}"], check=True, capture_output=True)
    stale = [name for name in GENERATED
             if (tmp_path / name).read_text(encoding="utf-8").splitlines()
             != (ROOT / "rtl" / name).read_text(encoding="utf-8").splitlines()]
    assert not stale, f"rtl/{', rtl/'.join(stale)} differ from rdl/usb_device.rdl: run make -C rdl regs"
