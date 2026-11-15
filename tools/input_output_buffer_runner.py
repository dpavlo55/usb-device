"""Build and run the input/output buffer cocotb test with NVC."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from cocotb_tools.runner import get_results, get_runner


ROOT = Path(__file__).resolve().parents[1]
TEST_DIR = ROOT / "tb" / "cocotb"
BUILD_DIR = ROOT / "sim" / "input_output_buffer_build"
SOURCES = [
    ROOT / "rtl" / "input_buffer.vhd",
    ROOT / "rtl" / "output_buffer.vhd",
    ROOT / "tb" / "vhdl" / "input_output_buffer_tb.vhd",
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--test-filter", help="Regular expression selecting cocotb tests")
    parser.add_argument(
        "--waves",
        dest="waves",
        action="store_true",
        default=True,
        help="Request simulator waveform output (the default)",
    )
    parser.add_argument(
        "--no-waves",
        dest="waves",
        action="store_false",
        help="Disable simulator waveform output",
    )
    parser.add_argument("--verbose", action="store_true", help="Show simulator commands")
    args = parser.parse_args()

    sys.path.insert(0, str(TEST_DIR))
    runner = get_runner("nvc")
    runner.build(
        sources=SOURCES,
        hdl_toplevel="input_output_buffer_tb",
        build_args=["--std=08"],
        build_dir=BUILD_DIR,
        clean=True,
        verbose=args.verbose,
    )
    results_file = runner.test(
        test_module=["test_input_output_buffer"],
        hdl_toplevel="input_output_buffer_tb",
        hdl_toplevel_lang="vhdl",
        build_dir=BUILD_DIR,
        test_dir=BUILD_DIR,
        test_filter=args.test_filter,
        test_args=["--dump-arrays"],
        waves=args.waves,
        verbose=args.verbose,
    )
    tests_run, tests_failed = get_results(results_file)
    if tests_run == 0 or tests_failed:
        raise SystemExit(
            f"input/output buffer regression failed: "
            f"{tests_failed} failed of {tests_run} test(s)"
        )


if __name__ == "__main__":
    main()