"""Reset awareness for every component of the environment.

The clock/reset agent's monitor publishes the state of each reset into a
:class:`ResetSignal`, shared through the ConfigDB under ``reset_<domain>``. A component
that mixes in :class:`ResetAware` runs its main loop only while its domain is out of
reset: when reset asserts, the loop is cancelled, ``on_reset()`` flushes the component's
state, and the loop starts again once reset is released.
"""
from __future__ import annotations

import cocotb
from cocotb.triggers import Event
from cocotb.utils import get_sim_time
from pyuvm import ConfigDB


class ResetSignal:
    """The observed state of one reset. Starts asserted: nothing runs before power-on reset."""

    def __init__(self, domain: str):
        self.domain = domain
        self.active = True
        self.count = 0
        self.asserted_at_fs = 0
        self._asserted = Event()
        self._released = Event()

    def assert_reset(self) -> None:
        if not self.active:
            self.active = True
            self.count += 1
            self.asserted_at_fs = round(get_sim_time(unit="fs"))
            self._released.clear()
            self._asserted.set()

    def release(self) -> None:
        if self.active:
            self.active = False
            self._asserted.clear()
            self._released.set()

    async def wait_released(self) -> None:
        if self.active:
            await self._released.wait()

    async def wait_asserted(self) -> None:
        if not self.active:
            await self._asserted.wait()

    def __repr__(self) -> str:
        return f"ResetSignal({self.domain!r}, active={self.active})"


def reset_key(domain: str) -> str:
    return f"reset_{domain}"


class ResetAware:
    """Mixin for ``uvm_component`` subclasses whose behaviour restarts on reset.

    Subclasses implement ``run_out_of_reset()`` instead of ``run_phase()`` and may
    override ``on_reset()``; ``reset_domain`` names the clock domain that resets them.
    """

    reset_domain = "sys"

    def build_phase(self):
        super().build_phase()
        self.reset: ResetSignal = ConfigDB().get(self, "", reset_key(self.reset_domain))

    async def run_phase(self):
        while True:
            await self.reset.wait_released()
            task = cocotb.start_soon(self.run_out_of_reset())
            await self.reset.wait_asserted()
            task.cancel()
            self.logger.info("reset asserted: flushing state")
            self.on_reset()

    async def run_out_of_reset(self) -> None:
        """The component's behaviour while its domain is out of reset."""

    def on_reset(self) -> None:
        """Drop everything in flight; called each time reset asserts."""
