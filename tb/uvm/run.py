"""Run the pyuvm Chapter 9 suite with NVC.

    python tb/uvm/run.py                      # all TD tests against the RTL
    python tb/uvm/run.py --dut reference      # against the behavioral reference device
    python tb/uvm/run.py --td 9.1 9.13 --seed 0x1234 --timing spec --waves
    python tb/uvm/run.py --dut reference --selfcheck   # prove the checkers catch bugs

The register model is regenerated from rdl/usb_device.rdl first, so the RDL stays the
single source of truth.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from cocotb_runner import SOURCES as REGRESSION_SOURCES  # noqa: E402  (the RTL compile order)
from cocotb_runner import RelaxedNvc  # noqa: E402
from cocotb_tools.runner import get_results  # noqa: E402

RAL_FILE = HERE / "usbtb" / "ral" / "usb_device_ral.py"
TESTS_DIR = HERE / "tests"
TD_PATTERN = re.compile(r"test_ch9_TD_9_(\d+)\.py")


def generate_register_model() -> None:
    subprocess.run([sys.executable, "-m", "peakrdl", "pyuvm", str(ROOT / "rdl" / "regblock_udps.rdl"),
                    str(ROOT / "rdl" / "usb_device.rdl"), "--type-style", "hier", "-o", str(RAL_FILE)],
                   check=True)


def available_tests() -> dict[str, str]:
    """TD number ("9.4") to test module name."""
    modules = {}
    for path in sorted(TESTS_DIR.glob("test_ch9_TD_9_*.py")):
        match = TD_PATTERN.fullmatch(path.name)
        if match:
            modules[f"9.{match.group(1)}"] = path.stem
    return dict(sorted(modules.items(), key=lambda item: int(item[0].split(".")[1])))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dut", choices=("rtl", "reference"), default="rtl")
    parser.add_argument("--td", nargs="+", metavar="9.N", help="TD numbers to run (default: all)")
    parser.add_argument("--seed", help="replay seed (decimal or 0x hex); default: fresh")
    parser.add_argument("--timing", choices=("fast", "spec"), default="fast")
    parser.add_argument("--selfcheck", action="store_true", help="run the checker self-tests (reference DUT)")
    parser.add_argument("--waves", action="store_true")
    parser.add_argument("--list", action="store_true", help="list the TD tests and exit")
    args = parser.parse_args()

    tests = available_tests()
    if args.list:
        print("\n".join(f"TD {td}: {module}" for td, module in tests.items()))
        return
    unknown = set(args.td or ()) - tests.keys()
    if unknown:
        parser.error(f"no test for TD {sorted(unknown)}; available: {', '.join(tests)}")
    modules = [tests[td] for td in (args.td or tests)]
    if args.selfcheck:
        if args.dut != "reference":
            parser.error("--selfcheck runs against the reference device (--dut reference)")
        modules.append("test_tb_selfcheck")

    generate_register_model()
    if args.dut == "rtl":
        top = "usb_top_uvm_tb"
        sources = [path for path in REGRESSION_SOURCES if path.parent == ROOT / "rtl"]
        sources.append(HERE / "hdl" / "usb_top_uvm_tb.vhd")
    else:
        top = "usb_reference_tb"
        sources = [HERE / "hdl" / "usb_reference_tb.vhd"]

    build_dir = ROOT / "sim" / "uvm_build" / args.dut
    build_dir.mkdir(parents=True, exist_ok=True)
    # cocotb's runner hands sys.path to the simulator's Python as PYTHONPATH.
    sys.path[:0] = [str(HERE), str(TESTS_DIR), str(ROOT / "tb" / "cocotb")]
    environment = {
        "DUT": args.dut,
        "TIMING": args.timing,
        "UVM_OUTPUT_DIR": str(build_dir),
    }
    if args.seed:
        environment["SEED"] = args.seed

    runner = RelaxedNvc()
    runner.build(sources=sources, hdl_toplevel=top, build_dir=build_dir, clean=True,
                 build_args=["--std=08", "--ieee-warnings=off"])
    results = runner.test(test_module=modules, hdl_toplevel=top, hdl_toplevel_lang="vhdl",
                          build_dir=build_dir, test_dir=build_dir, extra_env=environment, waves=args.waves)
    tests_run, failed = get_results(results)
    print(f"\n{tests_run - failed}/{tests_run} tests passed (DUT={args.dut}, TIMING={args.timing}); "
          f"coverage databases in {build_dir}")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
