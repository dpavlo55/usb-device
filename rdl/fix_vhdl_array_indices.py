"""Fix fixed array indices emitted by peakrdl-regblock-vhdl 1.3.1.1."""

import argparse
from pathlib import Path
import re


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert VHDL array indices [N] to (N).")
    parser.add_argument("vhdl_file", type=Path, help="Generated VHDL file to update in place")
    args = parser.parse_args()

    # Process bytes so the generated file's encoding and line endings stay intact.
    original = args.vhdl_file.read_bytes()
    corrected, count = re.subn(rb"\[([0-9]+)\]", rb"(\1)", original)
    if count:
        args.vhdl_file.write_bytes(corrected)
    print(f"{args.vhdl_file}: corrected {count} array index(es)")


if __name__ == "__main__":
    main()
