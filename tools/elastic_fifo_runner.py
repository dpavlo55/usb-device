"""Run elastic_fifo tests at ADDR_WIDTH 1-5 (capacities 2, 4, 8, 16, and 32)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from cocotb_tools.runner import get_results, get_runner


ROOT = Path(__file__).resolve().parents[1]
TEST_DIR = ROOT / "tb" / "cocotb"
BUILD_ROOT = ROOT / "sim" / "elastic_fifo_build"
ADDR_WIDTHS = (1, 2, 3, 4, 5)
DEPTHS = tuple(1 << addr_width for addr_width in ADDR_WIDTHS)
SOURCES = [
    ROOT / "rtl" / "stream_pkg.vhd",
    ROOT / "rtl" / "input_buffer.vhd",
    ROOT / "rtl" / "output_buffer.vhd",
    ROOT / "rtl" / "elastic_fifo.vhd",
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument(
        "--addr-width", type=int, choices=ADDR_WIDTHS,
        help="Run just this address width (default: run all five address widths)",
    )
    selection.add_argument(
        "--depth", type=int, choices=DEPTHS,
        help="Select FIFO memory depth, converted to ADDR_WIDTH (e.g. --depth 8 uses width 3)",
    )
    parser.add_argument("--test-filter", help="Regular expression selecting cocotb tests")
    parser.add_argument("--waves", dest="waves", action="store_true", default=True)
    parser.add_argument("--no-waves", dest="waves", action="store_false")
    parser.add_argument("--verbose", action="store_true", help="Show simulator commands")
    args = parser.parse_args()

    sys.path.insert(0, str(TEST_DIR))
    total_run = total_failed = 0
    failed_depths = []
    selected_width = args.addr_width
    if args.depth is not None:
        selected_width = args.depth.bit_length() - 1
    widths = (selected_width,) if selected_width is not None else ADDR_WIDTHS
    for addr_width in widths:
        depth = 1 << addr_width
        build_dir = BUILD_ROOT / f"depth_{depth}"
        runner = get_runner("nvc")
        runner.build(
            sources=SOURCES,
            hdl_toplevel="elastic_fifo",
            build_args=["--std=08"],
            build_dir=build_dir,
            always=True,
            verbose=args.verbose,
        )
        results_file = runner.test(
            test_module="test_elastic_fifo",
            hdl_toplevel="elastic_fifo",
            hdl_toplevel_lang="vhdl",
            parameters={"ADDR_WIDTH": addr_width},
            extra_env={"ELASTIC_FIFO_ADDR_WIDTH": str(addr_width)},
            build_dir=build_dir,
            test_dir=build_dir,
            test_filter=args.test_filter,
            test_args=["--dump-arrays"],
            waves=args.waves,
            verbose=args.verbose,
        )
        tests_run, tests_failed = get_results(results_file)
        total_run += tests_run
        total_failed += tests_failed
        if tests_run == 0 or tests_failed:
            failed_depths.append(depth)
        print(f"ADDR_WIDTH={addr_width} (memory={depth}, total capacity={depth + 2}): "
              f"{tests_run - tests_failed}/{tests_run} tests passed")

    if failed_depths:
        raise SystemExit(
            f"elastic_fifo regression failed: {total_failed}/{total_run} tests failed; "
            f"unsuccessful depths={failed_depths}"
        )
    print(f"elastic_fifo regression passed: {total_run} tests")


if __name__ == "__main__":
    main()