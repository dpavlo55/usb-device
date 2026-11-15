"""pytest setup for the pure-Python unit tests in ``unit/``.

The environment reuses the USB codec of ``tb/cocotb/models``; both directories go on the
import path exactly as ``run.py`` sets them for the simulator.
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
for path in (HERE, HERE.parent / "cocotb"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
