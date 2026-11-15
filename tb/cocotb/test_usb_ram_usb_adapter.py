"""Black-box verification of the USB-side shared RAM adapter.

The adapter only moves streams to and from the shared RAM; ownership and
endpoint configuration belong to the endpoint controller. The harness
(``usb_ram_usb_adapter_tb.vhd``) contains the adapter and the shared
``usb_ram``. The testbench drives and observes only external ports:

* ``base_address`` / ``write_allowed`` per endpoint (what the controller
  would drive),
* ``rx_payload`` (OUT data) through a driver that applies every accepted beat
  to the reference model, and the OUT reports, one per packet,
* ``read_command`` (one-cycle ``start``; fields are randomized at all other
  times so the DUT must capture them on ``start``),
* ``tx_payload`` (IN data) through a monitor feeding a scoreboard and checking
  the AXI4-Stream rule that a stalled beat must not change or disappear, and
  that the IN report appears in exactly the cycle the last beat is taken,
* the RAM's SW port as a backdoor: the whole RAM is preloaded with random bytes
  at the start and compared against the model at the end of every test.

Errors are collected and reported together at the end of each test. ``SEED``
replays a run; otherwise a fresh seed is drawn and printed with any failure.
"""
from __future__ import annotations

import os
import random
from collections import deque
from collections.abc import Callable, Sequence
from fractions import Fraction

import cocotb
from cocotb.triggers import ReadOnly, ReadWrite, RisingEdge

from drivers.axis_utils import THROTTLE_MODES, Throttle, beats_differ, resolved
from drivers.clock_reset import ClockDomain, start_clocks_and_reset
from models.usb_ram_adapter_model import (
    NUM_ENDPOINTS,
    InOrderScoreboard,
    PacketReport,
    ReadCommand,
    StreamBeat,
    TxScoreboard,
    UsbRamAdapterModel,
    compare_ram,
    out_packet_beats,
)

ADDR_WIDTH = 10
RAM_SIZE = 1 << ADDR_WIDTH
SLOT_SIZE = RAM_SIZE // NUM_ENDPOINTS
MAX_BASE_OFFSET = 15
# The adapter's input buffer holds at most a few beats, so once this many
# beats of a packet have been taken on rx_payload its first beat has been
# processed internally.
FIRST_BEAT_MARGIN = 16


def _seed_from_environment() -> int:
    value = os.environ.get("SEED")
    return int(value, 0) if value is not None else random.SystemRandom().getrandbits(32)




def random_regions(rng: random.Random) -> tuple[list[int], list[int]]:
    """Disjoint, unaligned endpoint regions; returns base addresses and capacities."""
    slots = list(range(NUM_ENDPOINTS))
    rng.shuffle(slots)
    base: list[int] = []
    capacity: list[int] = []
    for endpoint in range(NUM_ENDPOINTS):
        offset = rng.randint(0, MAX_BASE_OFFSET)
        base.append(slots[endpoint] * SLOT_SIZE + offset)
        capacity.append(SLOT_SIZE - offset)
    return base, capacity


class AdapterEnv:
    """Drivers, monitors, reference model and scoreboards around the harness."""

    def __init__(self, dut, name: str) -> None:
        self.dut = dut
        self.seed = _seed_from_environment()
        self.rng = random.Random(self.seed)
        self.name = name
        context = f"{name}, SEED={self.seed:#x}"
        self.model = UsbRamAdapterModel(ADDR_WIDTH)
        self.scoreboard = TxScoreboard(context=context)
        self.out_reports = InOrderScoreboard("OUT report", context)
        self.in_reports = InOrderScoreboard("IN report", context)
        self.errors: list[str] = []
        self.rx_queue: deque[StreamBeat] = deque()
        self.rx_in_flight = False
        self.rx_throttle = Throttle(self.rng, "always")
        self.tx_throttle = Throttle(self.rng, "always")
        self.rx_stall_cycles = 0
        self.rx_beats_taken = 0
        self.tx_beats_seen = 0
        self.write_allowed = [False] * NUM_ENDPOINTS
        # Called with each OUT report the DUT emits (before the next cycle's drive).
        self.on_out_report: Callable[[PacketReport], None] | None = None
        # Called with each OUT report the model predicts.
        self.on_model_report: Callable[[PacketReport], None] | None = None

    # ------------------------------------------------------------------ setup
    async def start(self) -> None:
        dut = self.dut
        for signal in (
            dut.base_address, dut.write_allowed,
            dut.read_endpoint, dut.read_base_address, dut.read_length, dut.read_start,
            dut.rx_payload_tdata, dut.rx_payload_tkeep, dut.rx_payload_tdest,
            dut.rx_payload_tlast, dut.rx_payload_tvalid, dut.tx_payload_tready,
            dut.sys_en, dut.sys_we, dut.sys_addr, dut.sys_wdata,
        ):
            signal.value = 0
        await start_clocks_and_reset(ClockDomain(dut.clk, dut.rst_n, Fraction(20833, 1000)))
        cocotb.start_soon(self._rx_driver())
        cocotb.start_soon(self._tx_monitor())
        cocotb.start_soon(self._out_report_monitor())
        await self.next_cycle()

    async def next_cycle(self) -> None:
        """Advance to the next rising edge; inputs may then be driven."""
        await RisingEdge(self.dut.clk)
        await ReadWrite()

    async def idle(self, cycles: int) -> None:
        for _ in range(cycles):
            await self.next_cycle()

    async def wait_for(
        self,
        condition: Callable[[], bool],
        cycles: int,
        what: str,
        paused: Callable[[], bool] = lambda: False,
    ) -> bool:
        """Wait up to ``cycles`` cycles, not counting cycles where ``paused()`` holds."""
        counted = 0
        for _ in range(100 * cycles + 100_000):
            if condition():
                return True
            if counted >= cycles:
                break
            if not paused():
                counted += 1
            await self.next_cycle()
        if condition():
            return True
        self.errors.append(f"timeout after {cycles} cycles waiting for {what}")
        return False

    def set_base_addresses(self, base: Sequence[int]) -> None:
        """Only while no OUT packet is in flight; call right after ``next_cycle``."""
        self.model.set_base_addresses(base)
        self.dut.base_address.value = sum(
            address << (ADDR_WIDTH * index) for index, address in enumerate(base)
        )

    def drive_write_allowed(self, allowed: Sequence[bool]) -> None:
        """Drive the DUT only; call right after ``next_cycle``."""
        self.write_allowed = [bool(a) for a in allowed]
        self.dut.write_allowed.value = sum(int(a) << index for index, a in enumerate(allowed))

    def set_write_allowed(self, allowed: Sequence[bool]) -> None:
        """Only while no OUT packet is in flight; call right after ``next_cycle``."""
        self.model.set_write_allowed(allowed)
        self.drive_write_allowed(allowed)

    # -------------------------------------------------------- RAM backdoor
    async def ram_preload_random(self) -> None:
        data = bytes(self.rng.randrange(256) for _ in range(RAM_SIZE))
        dut = self.dut
        for word in range(RAM_SIZE // 4):
            await self.next_cycle()
            dut.sys_en.value = 1
            dut.sys_we.value = 0xF
            dut.sys_addr.value = word
            dut.sys_wdata.value = int.from_bytes(data[4 * word:4 * word + 4], "little")
        await self.next_cycle()
        dut.sys_en.value = 0
        dut.sys_we.value = 0
        self.model.load(0, data)

    async def ram_read_all(self) -> bytes:
        dut = self.dut
        data = bytearray()
        for word in range(RAM_SIZE // 4):
            await self.next_cycle()
            dut.sys_en.value = 1
            dut.sys_we.value = 0
            dut.sys_addr.value = word
            await self.next_cycle()
            dut.sys_en.value = 0
            await ReadOnly()
            try:
                value = resolved(dut.sys_rdata.value, "sys_rdata")
            except ValueError as error:
                self.errors.append(f"RAM word {word}: {error}")
                value = 0
            data += value.to_bytes(4, "little")
        return bytes(data)

    async def check_ram(self) -> None:
        observed = await self.ram_read_all()
        self.errors.extend(compare_ram(self.model.ram, observed, f"{self.name}, SEED={self.seed:#x}"))

    # ---------------------------------------------------------------- OUT
    def send_out_packet(self, dest: int, payload: bytes) -> None:
        self.rx_queue.extend(out_packet_beats(dest, payload))

    @property
    def rx_idle(self) -> bool:
        return not self.rx_queue and not self.rx_in_flight

    async def wait_rx_drained(self, beats: int) -> None:
        await self.wait_for(lambda: self.rx_idle, 16 * beats + 256, "rx_payload to be consumed")
        await self.wait_for(lambda: self.out_reports.outstanding == 0, 64, "OUT reports")

    async def _rx_driver(self) -> None:
        dut = self.dut
        beat: StreamBeat | None = None
        accepted = False
        while True:
            await self.next_cycle()
            if accepted:
                self.rx_beats_taken += 1
                report = self.model.out_beat(beat)
                if report is not None:
                    self.out_reports.expect(report)
                    if self.on_model_report is not None:
                        self.on_model_report(report)
                beat = None
                self.rx_in_flight = False
            # A stalled beat stays offered unchanged; a new one waits for the throttle.
            if beat is None and self.rx_queue and self.rx_throttle():
                beat = self.rx_queue.popleft()
                self.rx_in_flight = True
            if beat is None:
                dut.rx_payload_tvalid.value = 0
                dut.rx_payload_tdata.value = self.rng.randrange(256)
                dut.rx_payload_tkeep.value = self.rng.randrange(2)
                dut.rx_payload_tdest.value = self.rng.randrange(NUM_ENDPOINTS)
                dut.rx_payload_tlast.value = self.rng.randrange(2)
            else:
                dut.rx_payload_tvalid.value = 1
                dut.rx_payload_tdata.value = beat.data
                dut.rx_payload_tkeep.value = beat.keep
                dut.rx_payload_tdest.value = beat.dest
                dut.rx_payload_tlast.value = beat.last
            await ReadOnly()
            try:
                ready = resolved(dut.rx_payload_tready.value, "rx_payload_tready")
            except ValueError as error:
                self.errors.append(str(error))
                ready = 0
            accepted = beat is not None and ready == 1
            if beat is not None and not ready:
                self.rx_stall_cycles += 1

    async def _out_report_monitor(self) -> None:
        dut = self.dut
        while True:
            await RisingEdge(dut.clk)
            await ReadOnly()
            try:
                valid = resolved(dut.out_report_valid.value, "out_report_valid")
                if not valid:
                    continue
                report = PacketReport(
                    endpoint=resolved(dut.out_report_endpoint.value, "out_report_endpoint"),
                    byte_count=resolved(dut.out_report_byte_count.value, "out_report_byte_count"),
                    accepted=bool(resolved(dut.out_report_accepted.value, "out_report_accepted")),
                )
            except ValueError as error:
                self.errors.append(str(error))
                continue
            self.out_reports.observe(report)
            if self.on_out_report is not None:
                self.on_out_report(report)

    # ----------------------------------------------------------------- IN
    async def send_read(self, command: ReadCommand) -> None:
        """Pulse ``start`` and wait until the IN packet and its report have been seen."""
        self.scoreboard.expect(self.model.in_packet(command))
        self.in_reports.expect(self.model.in_report(command))
        target = self.scoreboard.completed_packets + self.scoreboard.outstanding
        dut = self.dut
        await self.next_cycle()
        dut.read_endpoint.value = command.endpoint
        dut.read_base_address.value = command.base_address
        dut.read_length.value = command.length
        dut.read_start.value = 1
        await self.next_cycle()
        dut.read_start.value = 0
        dut.read_endpoint.value = self.rng.randrange(NUM_ENDPOINTS)
        dut.read_base_address.value = self.rng.randrange(RAM_SIZE)
        dut.read_length.value = self.rng.randrange(1024)
        # OUT has strict priority, so reads may legitimately wait while OUT
        # beats are pending; those cycles do not count towards the timeout.
        await self.wait_for(
            lambda: self.scoreboard.completed_packets >= target and self.in_reports.outstanding == 0,
            16 * command.length + 256,
            f"IN packet {command}",
            paused=lambda: not self.rx_idle,
        )

    async def _tx_monitor(self) -> None:
        dut = self.dut
        stalled: StreamBeat | None = None
        while True:
            await self.next_cycle()
            ready = int(self.tx_throttle())
            dut.tx_payload_tready.value = ready
            await ReadOnly()
            beat = None
            try:
                valid = resolved(dut.tx_payload_tvalid.value, "tx_payload_tvalid")
                if valid:
                    keep = resolved(dut.tx_payload_tkeep.value, "tx_payload_tkeep")
                    data_value = dut.tx_payload_tdata.value
                    beat = StreamBeat(
                        data=int(data_value) if data_value.is_resolvable else None,
                        keep=keep,
                        dest=resolved(dut.tx_payload_tdest.value, "tx_payload_tdest"),
                        last=resolved(dut.tx_payload_tlast.value, "tx_payload_tlast"),
                    )
                report_valid = resolved(dut.in_report_valid.value, "in_report_valid")
                if report_valid:
                    report = PacketReport(
                        endpoint=resolved(dut.in_report_endpoint.value, "in_report_endpoint"),
                        byte_count=resolved(dut.in_report_byte_count.value, "in_report_byte_count"),
                        accepted=bool(resolved(dut.in_report_accepted.value, "in_report_accepted")),
                    )
                    self.in_reports.observe(report)
            except ValueError as error:
                self.errors.append(str(error))
                stalled = None
                continue
            last_taken = beat is not None and ready and beat.last
            if bool(report_valid) != bool(last_taken):
                self.errors.append(
                    f"in_report_valid = {report_valid} in a cycle where the last IN beat was "
                    f"{'' if last_taken else 'not '}taken"
                )
            if beat is None:
                if stalled is not None:
                    self.errors.append(f"tx_payload_tvalid dropped before beat {stalled} was accepted")
                    stalled = None
                continue
            if stalled is not None and beats_differ(stalled, beat):
                self.errors.append(f"stalled tx beat changed from {stalled} to {beat}")
            if ready:
                self.tx_beats_seen += 1
                self.scoreboard.observe(beat)
                stalled = None
            else:
                stalled = beat

    # ------------------------------------------------------------- report
    async def finish(self) -> None:
        await self.idle(32)
        if not self.rx_idle:
            self.errors.append(f"{len(self.rx_queue)} rx beats were never consumed")
        await self.check_ram()
        self.scoreboard.check_complete()
        self.out_reports.check_complete()
        self.in_reports.check_complete()
        errors = self.errors + self.scoreboard.errors + self.out_reports.errors + self.in_reports.errors
        if errors:
            listing = "\n  ".join(errors[:40])
            more = f"\n  ... and {len(errors) - 40} more" if len(errors) > 40 else ""
            raise AssertionError(
                f"{self.name}: {len(errors)} error(s), SEED={self.seed:#x}\n  {listing}{more}"
            )
        self.dut._log.info("%s passed, SEED=%#x", self.name, self.seed)



def _payload(rng: random.Random, capacity: int) -> bytes:
    length = rng.choice((0, 1, rng.randint(2, capacity), capacity))
    return bytes(rng.randrange(256) for _ in range(length))


async def _run_out_packets(env: AdapterEnv, packets: Sequence[tuple[int, bytes]]) -> None:
    for dest, payload in packets:
        env.send_out_packet(dest, payload)
    await env.wait_rx_drained(sum(max(1, len(payload)) for _, payload in packets))


async def _run_reads(env: AdapterEnv, commands: Sequence[ReadCommand]) -> None:
    for command in commands:
        await env.send_read(command)
        await env.idle(env.rng.randint(0, 3))


async def _setup(env: AdapterEnv, allowed: Callable[[int], bool] = lambda _: True) -> list[int]:
    await env.start()
    await env.ram_preload_random()
    base, capacity = random_regions(env.rng)
    await env.next_cycle()
    env.set_base_addresses(base)
    env.set_write_allowed([allowed(k) for k in range(NUM_ENDPOINTS)])
    env.base = base
    return capacity


def _read(env: AdapterEnv, endpoint: int, capacity: list[int], length: int | None = None) -> ReadCommand:
    if length is None:
        length = len(_payload(env.rng, capacity[endpoint]))
    return ReadCommand(endpoint, env.base[endpoint], length)


# ======================================================================= tests
@cocotb.test()
async def test_reset_state(dut) -> None:
    """Without stimulus the adapter emits no IN beat and no report."""
    env = AdapterEnv(dut, "reset state")
    await env.start()
    await env.ram_preload_random()
    for _ in range(32):
        await env.next_cycle()
        await ReadOnly()
        for name in ("tx_payload_tvalid", "out_report_valid", "in_report_valid"):
            value = getattr(dut, name).value
            if not value.is_resolvable or int(value):
                env.errors.append(f"{name} = {value} without stimulus")
        if env.errors:
            break
    await env.finish()


@cocotb.test()
async def test_out_packets_to_all_endpoints(dut) -> None:
    """With writing allowed, OUT packets on every tdest land at base_address(tdest) + i and are reported."""
    env = AdapterEnv(dut, "OUT packets")
    capacity = await _setup(env)
    for mode in THROTTLE_MODES:
        env.rx_throttle = Throttle(env.rng, mode)
        packets = []
        for _ in range(24):
            dest = env.rng.randrange(NUM_ENDPOINTS)
            packets.append((dest, _payload(env.rng, capacity[dest])))
        await _run_out_packets(env, packets)
    await env.finish()


@cocotb.test()
async def test_out_packets_not_allowed(dut) -> None:
    """OUT packets for endpoints without write permission are dropped and reported as not accepted."""
    env = AdapterEnv(dut, "OUT packets not allowed")
    capacity = await _setup(env)
    for mode in ("always", "random", "bursty"):
        env.rx_throttle = Throttle(env.rng, mode)
        await env.next_cycle()
        env.set_write_allowed([env.rng.random() < 0.5 for _ in range(NUM_ENDPOINTS)])
        targets = list(range(NUM_ENDPOINTS)) * 2
        env.rng.shuffle(targets)
        await _run_out_packets(env, [(dest, _payload(env.rng, capacity[dest])) for dest in targets])
    await env.finish()


@cocotb.test()
async def test_write_permission_sampled_at_first_beat(dut) -> None:
    """write_allowed changing in the middle of a packet does not affect that packet."""
    env = AdapterEnv(dut, "write permission sampled at first beat")
    capacity = await _setup(env, lambda _: False)
    for round_index in range(8):
        endpoint = env.rng.randrange(NUM_ENDPOINTS)
        allowed_at_start = bool(round_index % 2)
        await env.next_cycle()
        allowed = [env.rng.random() < 0.5 for _ in range(NUM_ENDPOINTS)]
        allowed[endpoint] = allowed_at_start
        env.set_write_allowed(allowed)
        length = env.rng.randint(2 * FIRST_BEAT_MARGIN, capacity[endpoint])
        payload = bytes(env.rng.randrange(256) for _ in range(length))
        start = env.rx_beats_taken
        env.send_out_packet(endpoint, payload)
        await env.wait_for(
            lambda: env.rx_beats_taken - start >= FIRST_BEAT_MARGIN, 16 * length, "the packet to start")
        # Flip only the DUT input; the model keeps the value sampled at the first beat.
        flipped = list(env.write_allowed)
        flipped[endpoint] = not allowed_at_start
        env.drive_write_allowed(flipped)
        await env.wait_rx_drained(length)
        await env.next_cycle()
        env.set_write_allowed(flipped)
    await env.finish()


@cocotb.test()
async def test_report_in_time_for_next_packet(dut) -> None:
    """A controller that clears write_allowed(k) on each accepted OUT report blocks the very next packet.

    The testbench reacts to a report one clock edge later, like a registered
    controller. Packets follow each other back to back, so the report must come
    no later than the cycle the packet's last beat is consumed.
    """
    env = AdapterEnv(dut, "report in time for next packet")
    capacity = await _setup(env)

    def controller(report: PacketReport) -> None:
        if report.accepted:
            allowed = list(env.write_allowed)
            allowed[report.endpoint] = False
            cocotb.start_soon(_drive_next_cycle(env, allowed))

    def model_controller(report: PacketReport) -> None:
        if report.accepted:
            allowed = list(env.model.write_allowed)
            allowed[report.endpoint] = False
            env.model.set_write_allowed(allowed)

    env.on_out_report = controller
    env.on_model_report = model_controller
    for mode in ("always", "always", "random"):
        env.rx_throttle = Throttle(env.rng, mode)
        await env.next_cycle()
        env.set_write_allowed([True] * NUM_ENDPOINTS)
        endpoints = env.rng.sample(range(NUM_ENDPOINTS), 4)
        packets = []
        for endpoint in endpoints:
            for _ in range(3):
                length = env.rng.choice((0, 1, env.rng.randint(2, 8)))
                packets.append((endpoint, bytes(env.rng.randrange(256) for _ in range(length))))
        await _run_out_packets(env, packets)
    await env.finish()


async def _drive_next_cycle(env: AdapterEnv, allowed: list[bool]) -> None:
    await env.next_cycle()
    env.drive_write_allowed(allowed)


@cocotb.test()
async def test_in_packets_from_all_endpoints(dut) -> None:
    """Read commands return length bytes from base_address with tdest = endpoint, and are reported."""
    env = AdapterEnv(dut, "IN packets")
    capacity = await _setup(env)
    for mode in THROTTLE_MODES:
        env.tx_throttle = Throttle(env.rng, mode)
        commands = []
        for _ in range(24):
            endpoint = env.rng.randrange(NUM_ENDPOINTS)
            base = env.rng.randrange(RAM_SIZE)
            commands.append(ReadCommand(endpoint, base, env.rng.choice((0, 1, env.rng.randint(2, 64)))))
        await _run_reads(env, commands)
    await env.finish()


@cocotb.test()
async def test_concurrent_out_and_in(dut) -> None:
    """OUT and IN traffic at the same time; data written in one phase is read back in the next."""
    env = AdapterEnv(dut, "concurrent OUT and IN")
    capacity = await _setup(env)
    writers = set(env.rng.sample(range(NUM_ENDPOINTS), NUM_ENDPOINTS // 2))
    for phase in range(6):
        env.rx_throttle = Throttle(env.rng, env.rng.choice(THROTTLE_MODES))
        env.tx_throttle = Throttle(env.rng, env.rng.choice(THROTTLE_MODES))
        if phase:
            writers = set(range(NUM_ENDPOINTS)) - writers
        readers = sorted(set(range(NUM_ENDPOINTS)) - writers)
        packets = [
            (dest, _payload(env.rng, capacity[dest]))
            for dest in (env.rng.choice(sorted(writers)) for _ in range(16))
        ]
        commands = [
            _read(env, endpoint, capacity)
            for endpoint in (env.rng.choice(readers) for _ in range(16))
        ]
        out_task = cocotb.start_soon(_run_out_packets(env, packets))
        in_task = cocotb.start_soon(_run_reads(env, commands))
        await out_task
        await in_task
    await env.finish()


@cocotb.test()
async def test_out_has_priority_over_in(dut) -> None:
    """With tx always ready, an OUT stream is never back-pressured by an IN read in progress."""
    env = AdapterEnv(dut, "OUT priority")
    capacity = await _setup(env)
    in_endpoint, out_endpoint = env.rng.sample(range(NUM_ENDPOINTS), 2)
    in_task = cocotb.start_soon(env.send_read(_read(env, in_endpoint, capacity, capacity[in_endpoint])))
    # Start the OUT stream once the IN packet is under way, so both compete for the RAM.
    await env.wait_for(lambda: env.tx_beats_seen > 0, 64, "the first IN beat")
    env.rx_stall_cycles = 0
    payload = bytes(env.rng.randrange(256) for _ in range(capacity[out_endpoint]))
    await _run_out_packets(env, [(out_endpoint, payload)])
    if env.rx_stall_cycles:
        env.errors.append(
            f"rx_payload_tready was low for {env.rx_stall_cycles} cycle(s) "
            "while an IN read competed with the OUT stream"
        )
    await in_task
    await env.finish()


@cocotb.test()
async def test_zero_length_packets(dut) -> None:
    """Zero-length packets in both directions, next to normal packets on the same endpoint."""
    env = AdapterEnv(dut, "zero-length packets")
    capacity = await _setup(env)
    out_a, out_b, in_a, in_b = env.rng.sample(range(NUM_ENDPOINTS), 4)
    data = lambda n: bytes(env.rng.randrange(256) for _ in range(n))
    await _run_out_packets(env, [
        (out_a, b""),
        (out_a, data(5)),
        (out_b, b""),
        (out_b, b""),
        (out_a, data(capacity[out_a])),
        (out_b, data(3)),
    ])
    await _run_reads(env, [
        _read(env, in_a, capacity, 0),
        _read(env, in_a, capacity, 4),
        _read(env, in_b, capacity, 0),
        _read(env, in_b, capacity, 0),
        _read(env, in_b, capacity, 7),
    ])
    await env.finish()
