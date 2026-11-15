"""Simulator-independent full-speed bus abstraction and deterministic fake port."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Protocol, Sequence

try:  # Works both as tb.cocotb.drivers and after cocotb's sys.path insertion.
    from ..models.usb_fs_codec import decode_packet, encode_packet
    from ..models.usb_fs_timing import FS_BIT_PERIOD_FS, FSTimingScheduler
    from ..models.usb_fs_types import LineState, Packet, WireCell
except ImportError:  # pragma: no cover - exercised by the cocotb loader layout
    from models.usb_fs_codec import decode_packet, encode_packet
    from models.usb_fs_timing import FS_BIT_PERIOD_FS, FSTimingScheduler
    from models.usb_fs_types import LineState, Packet, WireCell


class BusContentionError(RuntimeError):
    """Two owners drove contradictory differential values."""


class UsbFsPort(Protocol):
    async def drive(self, owner: str, state: LineState) -> None: ...
    async def release(self, owner: str) -> None: ...
    async def sample(self) -> LineState: ...
    async def time_fs(self) -> int: ...
    async def wait_fs(self, duration_fs: int) -> None: ...


@dataclass(frozen=True, slots=True)
class BusSample:
    time_fs: int
    state: LineState


@dataclass(frozen=True, slots=True)
class TraceEntry:
    time_fs: int
    action: str
    owner: str
    state: LineState


@dataclass(frozen=True, slots=True)
class UsbFsTrace:
    entries: tuple[TraceEntry, ...] = ()

    def __iter__(self):
        return iter(self.entries)


class UsbFsBus:
    """Owner-aware bus facade; it never accesses DUT or cocotb attributes."""

    def __init__(self, port: UsbFsPort, owner: str = "host") -> None:
        if not owner:
            raise ValueError("owner must not be empty")
        self.port = port
        self.owner = owner

    async def drive(self, state: LineState, owner: str | None = None) -> None:
        await self.port.drive(owner or self.owner, state)

    async def drive_cells(
        self,
        cells: Iterable[WireCell | LineState],
        owner: str | None = None,
        scheduler: FSTimingScheduler | None = None,
    ) -> None:
        schedule = scheduler or FSTimingScheduler()
        for index, cell in enumerate(cells):
            state = cell.state if isinstance(cell, WireCell) else cell
            await self.drive(state, owner)
            await self.port.wait_fs(schedule.period_fs(index))
        await self.release(owner)

    async def release(self, owner: str | None = None) -> None:
        await self.port.release(owner or self.owner)

    async def sample(self) -> BusSample:
        return BusSample(await self.port.time_fs(), await self.port.sample())

    async def time_fs(self) -> int:
        return await self.port.time_fs()

    async def wait_for_sop(self, timeout_cells: int = 32) -> BusSample | None:
        """Wait for the K that starts a packet.

        A port that can wait for line edges (``wait_for_state``) finds the K
        exactly and then moves half a bit on, so this and every later sample of
        the packet lands mid-bit, like a receiver resynchronized to SOP. Other
        ports are polled once per bit.
        """
        if timeout_cells < 0:
            raise ValueError("timeout_cells must not be negative")
        wait_for_state = getattr(self.port, "wait_for_state", None)
        if wait_for_state is not None:
            if not await wait_for_state(LineState.K, timeout_cells * FS_BIT_PERIOD_FS):
                return None
            await self.port.wait_fs(FS_BIT_PERIOD_FS // 2)
            return await self.sample()
        for _ in range(timeout_cells):
            sample = await self.sample()
            if sample.state is LineState.K:
                return sample
            await self.port.wait_fs(FSTimingScheduler().period_fs(0))
        return None

    async def capture_cells(
        self,
        timeout_cells: int = 1024,
        *,
        sop_timeout_cells: int | None = None,
    ) -> tuple[WireCell, ...]:
        """Capture one packet, including its EOP, after waiting for its SOP."""
        if timeout_cells < 1:
            raise ValueError("timeout_cells must be positive")
        first = await self.wait_for_sop(
            timeout_cells if sop_timeout_cells is None else sop_timeout_cells
        )
        if first is None:
            return ()
        cells = [WireCell(first.state, first.time_fs)]
        schedule = FSTimingScheduler()
        se0 = 0
        for index in range(timeout_cells - 1):
            await self.port.wait_fs(schedule.period_fs(index))
            sample = await self.sample()
            cells.append(WireCell(sample.state, sample.time_fs))
            if sample.state is LineState.SE0:
                se0 += 1
            elif se0 >= 2 and sample.state is LineState.J:
                return tuple(cells)
            else:
                se0 = 0
        return tuple(cells)

    async def capture_packet(
        self,
        timeout_cells: int = 1024,
        *,
        sop_timeout_cells: int | None = None,
    ):
        cells = await self.capture_cells(timeout_cells, sop_timeout_cells=sop_timeout_cells)
        return decode_packet(cells) if cells else None


class FakeUsbFsPort:
    """Deterministic in-memory port suitable for pure asyncio/pytest tests."""

    def __init__(self, *, strict: bool = True, diagnostic: bool = False) -> None:
        self.strict = strict
        self.diagnostic = diagnostic
        self._owners: dict[str, LineState] = {}
        self._rx: list[LineState] = []
        self._time_fs = 0
        self._trace: list[TraceEntry] = []
        self.contentions: list[str] = []

    @property
    def trace(self) -> UsbFsTrace:
        return UsbFsTrace(tuple(self._trace))

    @property
    def owners(self) -> tuple[str, ...]:
        return tuple(sorted(self._owners))

    def enqueue_cells(self, cells: Iterable[WireCell | LineState]) -> None:
        self._rx.extend(cell.state if isinstance(cell, WireCell) else cell for cell in cells)

    inject_cells = enqueue_cells

    def enqueue_packet(self, packet: Packet, **kwargs) -> None:
        self.enqueue_cells(encode_packet(packet, **kwargs))

    enqueue_response = enqueue_packet

    async def drive(self, owner: str, state: LineState) -> None:
        if not owner:
            raise ValueError("owner must not be empty")
        if not isinstance(state, LineState):
            raise TypeError("state must be a LineState")
        others = [value for key, value in self._owners.items() if key != owner]
        if others and any(value is not state for value in others):
            message = f"bus contention: {owner} drove {state.value} against {[v.value for v in others]}"
            self.contentions.append(message)
            if self.strict and not self.diagnostic:
                raise BusContentionError(message)
        self._owners[owner] = state
        await self._record("drive", owner, state)

    async def release(self, owner: str) -> None:
        state = self._owners.pop(owner, LineState.J)
        await self._record("release", owner, state)

    async def sample(self) -> LineState:
        if self._owners:
            values = tuple(self._owners.values())
            state = values[0] if all(value is values[0] for value in values) else LineState.SE1
            if state is LineState.SE1 and self.strict and not self.diagnostic:
                raise BusContentionError("sampling bus contention as SE1")
        elif self._rx:
            state = self._rx.pop(0)
        else:
            state = LineState.J
        await self._record("sample", "observer", state)
        return state

    async def time_fs(self) -> int:
        return self._time_fs

    async def wait_fs(self, duration_fs: int) -> None:
        if duration_fs < 0:
            raise ValueError("duration must not be negative")
        self._time_fs += duration_fs

    async def _record(self, action: str, owner: str, state: LineState) -> None:
        self._trace.append(TraceEntry(self._time_fs, action, owner, state))

    def clear(self) -> None:
        self._trace.clear()
        self.contentions.clear()


class CocotbUsbFsPort:
    """Bind :class:`UsbFsPort` to the resolved Cocotb USB bus.

    The testbench owns no clock or reset process.  Cocotb drives the clock,
    reset, and the external host-drive handles; the resolved bus handles are
    sampled after VHDL applies device bias and DUT drivers.
    """

    def __init__(
        self,
        dut,
        *,
        dp_name: str = "usb_dp",
        dn_name: str = "usb_dn",
        dp_drive_name: str | None = None,
        dn_drive_name: str | None = None,
    ) -> None:
        self._dp = getattr(dut, dp_name)
        self._dn = getattr(dut, dn_name)
        self._dp_drive = getattr(dut, dp_drive_name or dp_name)
        self._dn_drive = getattr(dut, dn_drive_name or dn_name)
        self.driven_cells: list[WireCell] = []

    async def drive(self, owner: str, state: LineState) -> None:
        if owner != "host":
            raise ValueError("the initial Cocotb port supports only the host owner")
        dp, dn = state.differential
        self._dp_drive.value = dp
        self._dn_drive.value = dn
        self.driven_cells.append(WireCell(state))

    async def release(self, owner: str) -> None:
        if owner != "host":
            raise ValueError("the initial Cocotb port supports only the host owner")
        self._dp_drive.value = "Z"
        self._dn_drive.value = "Z"

    async def sample(self) -> LineState:
        def logic(signal) -> int:
            value = str(signal.value).upper()
            if value in {"0", "L"}:
                return 0
            if value in {"1", "H"}:
                return 1
            raise BusContentionError(f"unresolved USB line value {value!r}")

        dp, dn = logic(self._dp), logic(self._dn)
        return next(state for state in LineState if state.differential == (dp, dn))

    async def time_fs(self) -> int:
        from cocotb.utils import get_sim_time

        return int(round(get_sim_time(unit="fs")))

    async def wait_for_state(self, state: LineState, timeout_fs: int) -> bool:
        """Wait until the resolved bus shows ``state``; False after ``timeout_fs``."""
        from cocotb.triggers import Edge, First, Timer

        deadline = await self.time_fs() + timeout_fs
        while True:
            try:
                if await self.sample() is state:
                    return True
            except BusContentionError:
                pass
            remaining = deadline - await self.time_fs()
            if remaining <= 0:
                return False
            timer = Timer(remaining, unit="fs")
            if await First(Edge(self._dp), Edge(self._dn), timer) is timer:
                return False

    async def wait_fs(self, duration_fs: int) -> None:
        if duration_fs < 0:
            raise ValueError("duration must not be negative")
        from cocotb.triggers import Timer

        await Timer(duration_fs, unit="fs")