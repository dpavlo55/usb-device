"""One seed for the whole run: every random source in the testbench derives from it.

The seed comes from the ``SEED`` environment variable (decimal or ``0x`` hex), the same
convention as the rest of the repository; without it a fresh 32-bit seed is drawn and
logged so a failing run can be replayed.
"""
from __future__ import annotations

import os
import random
import secrets
from typing import TypeVar

import vsc

T = TypeVar("T")


def resolve_seed(environ: dict[str, str] | None = None) -> int:
    value = (environ if environ is not None else os.environ).get("SEED", "").strip()
    return int(value, 0) if value else secrets.randbits(32)


def seeded(obj: T, seed: int, stream: str) -> T:
    """Give a pyvsc random object its own reproducible stream, independent of call order."""
    obj.set_randstate(vsc.RandState.mkFromSeed(seed, stream))
    return obj


def rng(seed: int, stream: str) -> random.Random:
    """A plain Python generator for one named stream (timing jitter, simple picks)."""
    return random.Random(f"{seed}:{stream}")
