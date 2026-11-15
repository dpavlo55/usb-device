"""usb_bus_state: suspend, resume, remote wakeup and SOF (USB 2.0 sections 7.1.7.6, 7.1.7.7).

Idle is the J state; K and SE0 are activity. While ``attached``, the block counts
idle clocks and sets ``suspended`` after ``SUSPEND_IDLE_CLOCKS`` of them (3 ms).
Any activity restarts the count. Activity while suspended clears ``suspended``
and pulses ``resume`` for one clock, so a bus reset during suspend gives
``resume`` too (any non-idle signaling resumes a device, section 7.1.7.7).

``remote_wakeup`` is accepted only while suspended. The request waits until the
bus has been idle for ``REMOTE_WAKEUP_IDLE_CLOCKS`` (TWTRSM, 5 ms), then
``resume_drive`` is high for exactly ``RESUME_DRIVE_CLOCKS`` (TDRSMUP, 2 ms) and
the line is ignored for ``RESUME_GUARD_CLOCKS`` after it, while the released
bus settles. The device stays suspended through the drive: only the host's own
resume K seen after the guard ends the suspend. Activity before the drive starts
cancels the request.

``sof`` pulses for one clock for a packet record with ``valid``, PID SOF, a good
PID check and a good CRC. Not attached: nothing is counted, ``suspended``,
``resume_drive`` and a pending request are cleared without a ``resume``.
"""
from __future__ import annotations

import cocotb
from cocotb.triggers import NextTimeStep, ReadOnly, RisingEdge

from drivers.clock_reset import ClockDomain, start_clocks_and_reset

SUSPEND_IDLE_CLOCKS = 300
REMOTE_WAKEUP_IDLE_CLOCKS = 500
RESUME_DRIVE_CLOCKS = 200
RESUME_GUARD_CLOCKS = 20
LATENCY = 2
PID_SOF, PID_OUT, PID_DATA0 = 0x5, 0x1, 0x3
J, K, SE0 = (1, 0), (0, 1), (0, 0)


class Harness:
    def __init__(self, dut) -> None:
        self.dut = dut
        self.cycle = 0
        self.resumes: list[int] = []
        self.sofs: list[int] = []
        self.drive_cycles: list[int] = []

    async def start(self) -> None:
        dut = self.dut
        self.line(J)
        dut.attached.value = 1
        dut.packet_valid.value = 0
        dut.packet_pid.value = PID_OUT
        dut.packet_pid_ok.value = 1
        dut.packet_crc_ok.value = 1
        dut.remote_wakeup.value = 0
        await start_clocks_and_reset(ClockDomain(dut.clk, dut.rst_n, 20))
        cocotb.start_soon(self._monitor())

    async def _monitor(self) -> None:
        while True:
            await RisingEdge(self.dut.clk)
            await ReadOnly()
            self.cycle += 1
            if int(self.dut.resume.value):
                self.resumes.append(self.cycle)
            if int(self.dut.sof.value):
                self.sofs.append(self.cycle)
            if int(self.dut.resume_drive.value):
                self.drive_cycles.append(self.cycle)

    def line(self, state: tuple[int, int]) -> None:
        self.dut.line_dp.value, self.dut.line_dn.value = state

    async def clocks(self, n: int) -> None:
        for _ in range(n):
            await RisingEdge(self.dut.clk)

    async def hold(self, state: tuple[int, int], n: int) -> None:
        self.line(state)
        await self.clocks(n)

    async def pulse(self, name: str) -> None:
        getattr(self.dut, name).value = 1
        await RisingEdge(self.dut.clk)
        getattr(self.dut, name).value = 0

    async def value(self, name: str) -> int:
        await ReadOnly()
        result = int(getattr(self.dut, name).value)
        await RisingEdge(self.dut.clk)
        return result

    async def wait_for(self, name: str, level: int, limit: int) -> int:
        """Clocks until @p name reads @p level, failing after @p limit."""
        for n in range(limit):
            await ReadOnly()
            if int(getattr(self.dut, name).value) == level:
                await NextTimeStep()
                return n
            await RisingEdge(self.dut.clk)
        raise AssertionError(f"{name} did not become {level} within {limit} clocks")

    async def suspend(self) -> None:
        """Activity, then idle until suspended."""
        await self.hold(K, 3)
        self.line(J)
        await self.wait_for("suspended", 1, SUSPEND_IDLE_CLOCKS + LATENCY + 1)


async def started(dut) -> Harness:
    harness = Harness(dut)
    await harness.start()
    return harness


@cocotb.test()
async def test_suspend_after_idle(dut) -> None:
    h = await started(dut)
    await h.hold(K, 3)
    h.line(J)
    clocks = await h.wait_for("suspended", 1, SUSPEND_IDLE_CLOCKS + LATENCY + 1)
    assert SUSPEND_IDLE_CLOCKS <= clocks <= SUSPEND_IDLE_CLOCKS + LATENCY, f"suspended after {clocks} idle clocks"
    assert not h.resumes, "no resume when entering suspend"
    await h.clocks(5 * SUSPEND_IDLE_CLOCKS)
    assert await h.value("suspended") == 1, "suspended holds on an idle bus"


@cocotb.test()
async def test_activity_keeps_awake(dut) -> None:
    h = await started(dut)
    for state in (K, SE0) * 4:
        await h.hold(J, SUSPEND_IDLE_CLOCKS - 10)
        await h.hold(state, 1)
        assert await h.value("suspended") == 0, "a frame of activity restarts the idle count"
    assert not h.resumes


@cocotb.test()
async def test_resume_on_k(dut) -> None:
    h = await started(dut)
    await h.suspend()
    h.line(K)
    clocks = await h.wait_for("suspended", 0, LATENCY + 2)
    assert clocks <= LATENCY, f"suspend ended {clocks} clocks after K"
    await h.hold(K, 50)
    await h.hold(J, 10)
    assert len(h.resumes) == 1, f"one resume pulse, got {h.resumes}"
    await h.suspend()
    assert len(h.resumes) == 1, "suspend is entered again after the next idle period"


@cocotb.test()
async def test_resume_on_bus_reset(dut) -> None:
    h = await started(dut)
    await h.suspend()
    await h.hold(SE0, 200)
    assert await h.value("suspended") == 0, "SE0 (bus reset) ends the suspend"
    assert len(h.resumes) == 1, f"a bus reset during suspend raises resume, got {h.resumes}"


@cocotb.test()
async def test_not_attached(dut) -> None:
    h = await started(dut)
    dut.attached.value = 0
    await h.hold(J, 3 * SUSPEND_IDLE_CLOCKS)
    assert await h.value("suspended") == 0, "no suspend while detached"
    dut.attached.value = 1
    await h.suspend()
    await h.pulse("remote_wakeup")
    dut.attached.value = 0
    await h.clocks(LATENCY + 1)
    assert await h.value("suspended") == 0, "detaching clears suspended"
    dut.attached.value = 1
    await h.hold(J, REMOTE_WAKEUP_IDLE_CLOCKS + RESUME_DRIVE_CLOCKS)
    assert not h.resumes, "detaching raises no resume"
    assert not h.drive_cycles, "detaching drops a pending remote wakeup"


@cocotb.test()
async def test_remote_wakeup_ignored_while_awake(dut) -> None:
    h = await started(dut)
    await h.hold(K, 3)
    h.line(J)
    await h.pulse("remote_wakeup")
    await h.clocks(SUSPEND_IDLE_CLOCKS + REMOTE_WAKEUP_IDLE_CLOCKS + RESUME_DRIVE_CLOCKS)
    assert await h.value("suspended") == 1
    assert not h.drive_cycles, "a request made before the suspend is not remembered"


@cocotb.test()
async def test_remote_wakeup_waits_for_idle(dut) -> None:
    """TWTRSM: requested right after suspend, K starts once the bus has been idle 5 ms."""
    h = await started(dut)
    await h.hold(K, 3)
    h.line(J)
    idle_start = h.cycle
    await h.wait_for("suspended", 1, SUSPEND_IDLE_CLOCKS + LATENCY + 1)
    await h.pulse("remote_wakeup")
    await h.wait_for("resume_drive", 1, REMOTE_WAKEUP_IDLE_CLOCKS)
    idle = h.cycle - idle_start
    assert REMOTE_WAKEUP_IDLE_CLOCKS <= idle <= REMOTE_WAKEUP_IDLE_CLOCKS + LATENCY + 1, f"K after {idle} idle clocks"
    await h.wait_for("resume_drive", 0, RESUME_DRIVE_CLOCKS + 1)
    assert len(h.drive_cycles) == RESUME_DRIVE_CLOCKS, f"K driven {len(h.drive_cycles)} clocks"
    assert h.drive_cycles == list(range(h.drive_cycles[0], h.drive_cycles[0] + RESUME_DRIVE_CLOCKS)), "one K pulse"
    await h.hold(J, 3 * RESUME_GUARD_CLOCKS)
    assert await h.value("suspended") == 1, "without the host's resume K the device stays suspended"
    assert not h.resumes


@cocotb.test()
async def test_remote_wakeup_after_idle(dut) -> None:
    h = await started(dut)
    await h.suspend()
    await h.clocks(REMOTE_WAKEUP_IDLE_CLOCKS)
    await h.pulse("remote_wakeup")
    clocks = await h.wait_for("resume_drive", 1, LATENCY + 2)
    assert clocks <= LATENCY, f"K starts {clocks} clocks after a request on a long-idle bus"


@cocotb.test()
async def test_remote_wakeup_host_answers(dut) -> None:
    """The host joins with K during the drive; the suspend ends after the guard, with resume."""
    h = await started(dut)
    await h.suspend()
    await h.pulse("remote_wakeup")
    await h.wait_for("resume_drive", 1, REMOTE_WAKEUP_IDLE_CLOCKS)
    await h.clocks(RESUME_DRIVE_CLOCKS // 2)
    h.line(K)
    await h.wait_for("resume_drive", 0, RESUME_DRIVE_CLOCKS)
    assert await h.value("suspended") == 1, "own K and the host's K during the drive are ignored"
    released = h.cycle
    await h.wait_for("suspended", 0, RESUME_GUARD_CLOCKS + LATENCY + 2)
    ended = h.cycle - released
    assert RESUME_GUARD_CLOCKS <= ended <= RESUME_GUARD_CLOCKS + LATENCY + 1, f"suspend ended {ended} clocks after release"
    await h.hold(K, 100)
    await h.hold(SE0, 2)
    await h.hold(J, 10)
    assert len(h.resumes) == 1, f"one resume for the host's resume signaling, got {h.resumes}"


@cocotb.test()
async def test_guard_ignores_settling(dut) -> None:
    h = await started(dut)
    await h.suspend()
    await h.pulse("remote_wakeup")
    await h.wait_for("resume_drive", 1, REMOTE_WAKEUP_IDLE_CLOCKS)
    await h.wait_for("resume_drive", 0, RESUME_DRIVE_CLOCKS + 1)
    await h.hold(SE0, RESUME_GUARD_CLOCKS // 2)
    await h.hold(J, 5 * RESUME_GUARD_CLOCKS)
    assert await h.value("suspended") == 1, "line glitches inside the guard do not resume"
    assert not h.resumes


@cocotb.test()
async def test_activity_cancels_pending_wakeup(dut) -> None:
    h = await started(dut)
    await h.suspend()
    await h.pulse("remote_wakeup")
    await h.hold(K, 10)
    await h.hold(J, SUSPEND_IDLE_CLOCKS + REMOTE_WAKEUP_IDLE_CLOCKS)
    assert len(h.resumes) == 1, "the host's activity resumed the device"
    assert not h.drive_cycles, "the pending request is dropped by the resume"


@cocotb.test()
async def test_sof(dut) -> None:
    h = await started(dut)
    cases = [  # pid, pid_ok, crc_ok, expected pulse
        (PID_SOF, 1, 1, True),
        (PID_SOF, 0, 1, False),
        (PID_SOF, 1, 0, False),
        (PID_OUT, 1, 1, False),
        (PID_DATA0, 1, 1, False),
        (PID_SOF, 1, 1, True),
    ]
    for pid, pid_ok, crc_ok, expected in cases:
        before = len(h.sofs)
        dut.packet_pid.value, dut.packet_pid_ok.value, dut.packet_crc_ok.value = pid, pid_ok, crc_ok
        await h.pulse("packet_valid")
        await h.clocks(LATENCY + 2)
        got = len(h.sofs) - before
        assert got == (1 if expected else 0), f"pid {pid:#x} pid_ok {pid_ok} crc_ok {crc_ok}: {got} SOF pulses"
