"""Build and run the standalone CRC cocotb checks with NVC."""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from cocotb_tools.runner import get_results, get_runner


ROOT = Path(__file__).resolve().parents[1]
TEST_DIR = ROOT / "tb" / "cocotb"
BUILD_ROOT = ROOT / "sim" / "crc_build"

CASES = {
    "crc5": {
        "top": "crc5_tb",
        "wrapper": ROOT / "tb" / "vhdl" / "crc5_tb.vhd",
        "test": "test_crc5_usb",
    },
    "crc16": {
        "top": "crc16_tb",
        "wrapper": ROOT / "tb" / "vhdl" / "crc16_tb.vhd",
        "test": "test_crc16_usb",
    },
}


def _run_case(name: str, test_filter: str | None, waves: bool, verbose: bool) -> None:
    case = CASES[name]
    build_dir = BUILD_ROOT / name
    runner = get_runner("nvc")
    runner.build(
        sources=[ROOT / "rtl" / "crc.vhd", case["wrapper"]],
        hdl_toplevel=case["top"],
        build_args=["--std=08"],
        build_dir=build_dir,
        clean=True,
        verbose=verbose,
    )
    results_file = runner.test(
        test_module=["test_crc"],
        hdl_toplevel=case["top"],
        hdl_toplevel_lang="vhdl",
        build_dir=build_dir,
        test_dir=build_dir,
        test_filter=test_filter or case["test"],
        test_args=["--dump-arrays"],
        extra_env={"CRC_COCOTB_VERBOSE": "1" if verbose else "0"},
        waves=waves,
        verbose=verbose,
    )
    tests_run, tests_failed = get_results(results_file)
    if tests_run == 0 or tests_failed:
        raise SystemExit(
            f"{name} CRC regression failed: "
            f"{tests_failed} failed of {tests_run} test(s)"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=["crc5", "crc16", "all"], default="all")
    parser.add_argument("--test-filter", help="Regular expression selecting cocotb tests")
    parser.add_argument("--no-waves", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    sys.path.insert(0, str(TEST_DIR))

    if args.case == "all" and args.test_filter:
        names = {
            name: case
            for name, case in CASES.items()
            if re.search(args.test_filter, case["test"])
        }
        if not names:
            parser.error("--test-filter does not select a CRC test")
    else:
        names = CASES if args.case == "all" else {args.case: CASES[args.case]}
    for name in names:
        _run_case(name, args.test_filter, not args.no_waves, args.verbose)


if __name__ == "__main__":
    main()