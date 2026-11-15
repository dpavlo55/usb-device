"""Run a selected firmware test until it reports a result."""
import cocotb
from cocotb.triggers import Event, FallingEdge, RisingEdge, Timer, with_timeout

from test_usb_top import start_clocks_and_release_reset


class PullUpToggles:
    """Counts the firmware's pull-up toggles (low, then high again) from the moment
    it is created, so a host busy on the bus cannot miss one; :meth:`next` waits for
    the next toggle not yet taken."""

    def __init__(self, dut) -> None:
        self.dut = dut
        self.seen = 0
        self.taken = 0
        self._toggled = Event()
        cocotb.start_soon(self._count())

    async def _count(self) -> None:
        while True:
            await FallingEdge(self.dut.usb_pu)
            await RisingEdge(self.dut.usb_pu)
            self.seen += 1
            self._toggled.set()

    async def next(self, timeout_ms: int = 5) -> None:
        self.taken += 1
        await with_timeout(self._wait(self.taken), timeout_ms, "ms")

    async def _wait(self, count: int) -> None:
        while self.seen < count:
            self._toggled.clear()
            await self._toggled.wait()


async def wait_for_sw_result(dut, timeout_ms: int = 5):
    """Wait for a firmware result while other cocotb stimulus may run."""
    for _ in range(timeout_ms * 1000):
        await Timer(1, unit="us")
        status = int(dut.sw_test_status.value)
        if status:
            assert status == 1, f"Firmware reported failure status {status}"
            return
    raise AssertionError(f"Firmware did not report a result within {timeout_ms} ms")


@cocotb.test()
async def test_sw_firmware(dut):
    await start_clocks_and_release_reset(dut, wait_for_pullup=False)
    await wait_for_sw_result(dut)
