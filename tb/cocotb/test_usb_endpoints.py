"""Black-box verification of the endpoint subsystem: ``usb_cdc`` + ``usb_endpoints``.

The harness (``usb_endpoints_tb.vhd``) has both clock domains. What software
would drive through the register block is driven here directly in the
``clk_sys`` domain: core enable, the per-endpoint configuration, the transfer
length with a one-cycle ``arm`` pulse, and the RAM's SW port. Transfer events
are observed in the ``clk_sys`` domain, as the register block would see them.
The ``clk_usb`` side gets OUT data on ``rx_payload``, routed by ``rx_route``
(entry and accept, as usb_protocol drives them after a token) and not by
``tdest``; IN requests arrive as a descriptor ``start``, and IN data leaves on
``tx_payload``. ``device_address`` crosses from ``clk_sys`` to ``clk_usb``.

The reference model (``models/usb_endpoints_model.py``) states the ownership
rules. Each test waits for the crossings to settle after software actions and
leaves realistic gaps between packets: on a real bus, packets are hundreds of
``clk_usb`` cycles apart.

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
from models.usb_endpoints_model import (
    DIRECTION_IN,
    DIRECTION_OUT,
    ISOCHRONOUS,
    EndpointConfig,
    TransferEvent,
    UsbEndpointsModel,
)
from models.usb_ram_adapter_model import (
    NUM_ENDPOINTS,
    InOrderScoreboard,
    StreamBeat,
    TxScoreboard,
    compare_ram,
    out_packet_beats,
)

ADDR_WIDTH = 10
RAM_SIZE = 1 << ADDR_WIDTH
SLOT_SIZE = RAM_SIZE // NUM_ENDPOINTS
MAX_BASE_OFFSET = 15
USB_PERIOD = Fraction(20833, 1000)
SYS_PERIODS = {"sys60": Fraction(16667, 1000), "sys20": Fraction(50)}
SETTLE_USB_CYCLES = 16
PACKET_GAP_USB_CYCLES = (16, 48)


def _seed_from_environment() -> int:
    value = os.environ.get("SEED")
    return int(value, 0) if value is not None else random.SystemRandom().getrandbits(32)


def random_config(
    rng: random.Random,
    direction: Callable[[int], int],
    enable: Callable[[int], bool] = lambda _: True,
) -> tuple[list[EndpointConfig], list[int]]:
    """Disjoint, unaligned endpoint regions; also returns each entry's capacity."""
    slots = list(range(NUM_ENDPOINTS))
    rng.shuffle(slots)
    config: list[EndpointConfig] = []
    capacity: list[int] = []
    for endpoint in range(NUM_ENDPOINTS):
        offset = rng.randint(0, MAX_BASE_OFFSET)
        config.append(EndpointConfig(slots[endpoint] * SLOT_SIZE + offset, enable(endpoint), direction(endpoint)))
        capacity.append(SLOT_SIZE - offset)
    return config, capacity


class EndpointsEnv:
    def __init__(self, dut, name: str, sys_period: Fraction = SYS_PERIODS["sys60"]) -> None:
        self.dut = dut
        self.sys_period = sys_period
        self.seed = _seed_from_environment()
        self.rng = random.Random(self.seed)
        self.name = f"{name} (Tsys={float(sys_period):.3f} ns)"
        context = f"{self.name}, SEED={self.seed:#x}"
        self.model = UsbEndpointsModel(ADDR_WIDTH)
        self.scoreboard = TxScoreboard(context=context)
        self.events = InOrderScoreboard("transfer event", context)
        self.errors: list[str] = []
        self.rx_queue: deque[tuple[StreamBeat, bool, bool]] = deque()
        self.rx_in_flight = False
        self.rx_throttle = Throttle(self.rng, "always")
        self.tx_throttle = Throttle(self.rng, "always")
        self.transfer_length = [0] * NUM_ENDPOINTS
        self.capacity = [SLOT_SIZE] * NUM_ENDPOINTS

    # ------------------------------------------------------------------ setup
    async def start(self) -> None:
        dut = self.dut
        for signal in (
            dut.enable_core, dut.endpoint_base_address, dut.endpoint_enable,
            dut.endpoint_direction, dut.transfer_length, dut.arm,
            dut.ram_req, dut.ram_addr, dut.ram_req_is_wr, dut.ram_wr_data, dut.ram_wr_biten,
            dut.descriptor_endpoint_number, dut.descriptor_start,
            dut.rx_payload_tdata, dut.rx_payload_tkeep, dut.rx_payload_tdest,
            dut.rx_payload_tlast, dut.rx_payload_tvalid, dut.tx_payload_tready,
            dut.rx_route_entry, dut.rx_route_accept, dut.rx_route_setup, dut.setup_received,
            dut.sys_device_address, dut.endpoint_number, dut.endpoint_stall, dut.reset_toggle,
            dut.out_complete, dut.out_overrun, dut.out_entry, dut.out_byte_count,
            dut.in_complete, dut.in_complete_entry, dut.endpoint_kind, dut.endpoint_size,
            dut.endpoint_split,
        ):
            signal.value = 0
        await start_clocks_and_reset(
            ClockDomain(dut.clk_sys, dut.rst_sys_n, self.sys_period),
            ClockDomain(dut.clk_usb, dut.rst_usb_n, USB_PERIOD),
        )
        cocotb.start_soon(self._rx_driver())
        cocotb.start_soon(self._tx_monitor())
        cocotb.start_soon(self._event_monitor())

    async def sys_cycle(self) -> None:
        await RisingEdge(self.dut.clk_sys)
        await ReadWrite()

    async def usb_cycle(self) -> None:
        await RisingEdge(self.dut.clk_usb)
        await ReadWrite()

    async def usb_idle(self, cycles: int) -> None:
        for _ in range(cycles):
            await self.usb_cycle()

    async def settle(self) -> None:
        """Let a software action cross into the USB domain."""
        await self.usb_idle(SETTLE_USB_CYCLES)

    async def gap(self) -> None:
        await self.usb_idle(self.rng.randint(*PACKET_GAP_USB_CYCLES))

    async def wait_for(self, condition: Callable[[], bool], cycles: int, what: str) -> bool:
        for _ in range(cycles):
            if condition():
                return True
            await self.usb_cycle()
        if condition():
            return True
        self.errors.append(f"timeout after {cycles} clk_usb cycles waiting for {what}")
        return False

    # ------------------------------------------------------------ software
    async def configure(self, config: Sequence[EndpointConfig], capacity: Sequence[int] | None = None) -> None:
        """Write the whole endpoint configuration in one clk_sys cycle."""
        await self.sys_cycle()
        self.model.configure(config)
        if capacity is not None:
            self.capacity = list(capacity)
        dut = self.dut
        dut.endpoint_base_address.value = sum(
            entry.base_address << (ADDR_WIDTH * index) for index, entry in enumerate(config))
        dut.endpoint_enable.value = sum(int(entry.enable) << index for index, entry in enumerate(config))
        dut.endpoint_direction.value = sum(entry.direction << index for index, entry in enumerate(config))
        dut.endpoint_number.value = sum(entry.endpoint_number << (4 * index) for index, entry in enumerate(config))
        dut.endpoint_kind.value = sum(entry.kind << (2 * index) for index, entry in enumerate(config))
        dut.endpoint_size.value = sum(entry.size << (3 * index) for index, entry in enumerate(config))
        dut.endpoint_split.value = sum(entry.split << (3 * index) for index, entry in enumerate(config))
        await self.settle()

    async def set_enable_core(self, enable: bool) -> None:
        await self.sys_cycle()
        self.model.set_enable_core(enable)
        self.dut.enable_core.value = int(enable)
        await self.settle()

    async def arm(self, endpoint: int, length: int = 0) -> None:
        """Write length and pulse arm in the same clk_sys cycle, as one register write does."""
        await self.sys_cycle()
        self.model.arm(endpoint, length)
        self.transfer_length[endpoint] = length
        self.dut.transfer_length.value = sum(
            value << (10 * index) for index, value in enumerate(self.transfer_length))
        self.dut.arm.value = 1 << endpoint
        await self.sys_cycle()
        self.dut.arm.value = 0
        await self.settle()

    async def ram_preload_random(self) -> None:
        data = bytes(self.rng.randrange(256) for _ in range(RAM_SIZE))
        dut = self.dut
        for word in range(RAM_SIZE // 4):
            await self.sys_cycle()
            dut.ram_req.value = 1
            dut.ram_req_is_wr.value = 1
            dut.ram_addr.value = 4 * word
            dut.ram_wr_data.value = int.from_bytes(data[4 * word:4 * word + 4], "little")
            dut.ram_wr_biten.value = 0xFFFF_FFFF
        await self.sys_cycle()
        dut.ram_req.value = 0
        dut.ram_req_is_wr.value = 0
        self.model.load(0, data)

    async def ram_read(self, address: int, length: int) -> bytes:
        """Bytes [address, address + length) over the SW port, word by word."""
        dut = self.dut
        data = bytearray()
        for word in range(address // 4, (address + length + 3) // 4):
            await self.sys_cycle()
            dut.ram_req.value = 1
            dut.ram_req_is_wr.value = 0
            dut.ram_addr.value = 4 * word
            await self.sys_cycle()
            dut.ram_req.value = 0
            await ReadOnly()
            data += int(dut.ram_rd_data.value).to_bytes(4, "little")
        offset = address % 4
        return bytes(data[offset:offset + length])

    async def ram_read_all(self) -> bytes:
        dut = self.dut
        data = bytearray()
        for word in range(RAM_SIZE // 4):
            await self.sys_cycle()
            dut.ram_req.value = 1
            dut.ram_req_is_wr.value = 0
            dut.ram_addr.value = 4 * word
            await self.sys_cycle()
            dut.ram_req.value = 0
            await ReadOnly()
            try:
                if not resolved(dut.ram_rd_ack.value, "ram_rd_ack"):
                    self.errors.append(f"no ram_rd_ack for RAM word {word}")
                value = resolved(dut.ram_rd_data.value, "ram_rd_data")
            except ValueError as error:
                self.errors.append(f"RAM word {word}: {error}")
                value = 0
            data += value.to_bytes(4, "little")
        return bytes(data)

    # ---------------------------------------------------------------- OUT
    @property
    def rx_idle(self) -> bool:
        return not self.rx_queue and not self.rx_in_flight

    async def out_packet(self, dest: int, payload: bytes, accept: bool = True, setup: bool = False,
                         result: bool = True) -> None:
        """Send one OUT packet to entry ``dest`` (``rx_route``), wait for it and its
        event, then leave a gap. ``accept=False`` is a packet for another device;
        ``setup=True`` follows a SETUP token.

        After the payload, the verdict usb_protocol would give follows on
        ``out_result``: complete for an active armed entry, overrun for an active
        entry that is not armed, nothing for another device or a SETUP. ``result=False``
        is a packet usb_protocol rejects without a verdict (e.g. longer than the buffer)."""
        self.rx_queue.extend((beat, accept, setup) for beat in out_packet_beats(dest, payload))
        await self.wait_for(lambda: self.rx_idle, 16 * max(1, len(payload)) + 256, "rx_payload to be consumed")
        if result and accept and not setup and self.model.active(dest, DIRECTION_OUT):
            await self.out_result(dest, len(payload), bool(self.model.armed[dest]))
        await self.gap()

    async def out_result(self, entry: int, byte_count: int, complete: bool) -> None:
        self.events.expect(self.model.out_result(entry, byte_count, complete))
        dut = self.dut
        await self.usb_cycle()
        dut.out_entry.value = entry
        dut.out_byte_count.value = byte_count
        dut.out_complete.value = int(complete)
        dut.out_overrun.value = int(not complete)
        await self.usb_cycle()
        dut.out_complete.value = 0
        dut.out_overrun.value = 0
        dut.out_entry.value = self.rng.randrange(NUM_ENDPOINTS)
        dut.out_byte_count.value = self.rng.randrange(1024)
        await self.wait_for(lambda: self.events.outstanding == 0, 256, f"the event for OUT packet to {entry}")

    async def _rx_driver(self) -> None:
        dut = self.dut
        beat: StreamBeat | None = None
        accept = True
        setup = False
        accepted = False
        while True:
            await self.usb_cycle()
            if accepted:
                self.model.out_beat(beat, accept, setup)
                beat = None
                self.rx_in_flight = False
            if beat is None and self.rx_queue and self.rx_throttle():
                beat, accept, setup = self.rx_queue.popleft()
                self.rx_in_flight = True
            # tdest is ignored; the route counts only at the first beat of a packet.
            dut.rx_payload_tdest.value = self.rng.randrange(NUM_ENDPOINTS)
            if beat is None:
                dut.rx_payload_tvalid.value = 0
                dut.rx_payload_tdata.value = self.rng.randrange(256)
                dut.rx_payload_tkeep.value = self.rng.randrange(2)
                dut.rx_payload_tlast.value = self.rng.randrange(2)
                dut.rx_route_entry.value = self.rng.randrange(NUM_ENDPOINTS)
                dut.rx_route_accept.value = self.rng.randrange(2)
                dut.rx_route_setup.value = self.rng.randrange(2)
            else:
                dut.rx_payload_tvalid.value = 1
                dut.rx_payload_tdata.value = beat.data
                dut.rx_payload_tkeep.value = beat.keep
                dut.rx_payload_tlast.value = beat.last
                dut.rx_route_entry.value = beat.dest
                dut.rx_route_accept.value = int(accept)
                dut.rx_route_setup.value = int(setup)
            await ReadOnly()
            try:
                ready = resolved(dut.rx_payload_tready.value, "rx_payload_tready")
            except ValueError as error:
                self.errors.append(str(error))
                ready = 0
            accepted = beat is not None and ready == 1

    # ----------------------------------------------------------------- IN
    async def setup_received(self, check_buffer: bool = False) -> None:
        """Pulse setup_received, as usb_protocol does once the SETUP data checks out.

        ``check_buffer`` reads the entry's first 8 buffer bytes over the SW port as
        soon as the event is seen: they must already hold the SETUP data."""
        self.events.expect(self.model.setup_received())
        entry = self.model._setup_entry
        base = self.model.config[entry].base_address
        expected = bytes(self.model.ram[base:base + 8])
        await self.usb_cycle()
        self.dut.setup_received.value = 1
        await self.usb_cycle()
        self.dut.setup_received.value = 0
        await self.wait_for(lambda: self.events.outstanding == 0, 256, "the SETUP event")
        if check_buffer:
            observed = await self.ram_read(base, 8)
            if observed != expected:
                self.errors.append(f"SETUP event before its data: buffer {observed.hex()}, expected {expected.hex()}")
        await self.gap()

    async def in_request(self, endpoint: int, ack: bool = True) -> None:
        """Pulse descriptor start and wait for the IN packet (or a quiet window).

        With ``ack`` the host's ACK follows, as usb_protocol reports it on
        ``in_result``; without it the entry stays armed and sends the same data
        on the next request. Isochronous entries complete without an ACK."""
        expected = self.model.in_request(endpoint)
        if expected is not None:
            beats, event = expected
            self.scoreboard.expect(beats)
            if event is not None:
                self.events.expect(event)
            target = self.scoreboard.completed_packets + self.scoreboard.outstanding
        dut = self.dut
        await self.usb_cycle()
        dut.descriptor_endpoint_number.value = endpoint
        dut.descriptor_start.value = 1
        await self.usb_cycle()
        dut.descriptor_start.value = 0
        dut.descriptor_endpoint_number.value = self.rng.randrange(NUM_ENDPOINTS)
        if expected is None:
            await self.usb_idle(64)
        else:
            await self.wait_for(
                lambda: self.scoreboard.completed_packets >= target and self.events.outstanding == 0,
                16 * len(beats) + 256,
                f"IN packet and event from endpoint {endpoint}",
            )
            if event is None and ack and self.model.config[endpoint].kind != ISOCHRONOUS:
                await self.in_result(endpoint)
        await self.gap()

    async def in_result(self, entry: int) -> None:
        event = self.model.in_result(entry)
        if event is not None:
            self.events.expect(event)
        dut = self.dut
        await self.usb_cycle()
        dut.in_complete_entry.value = entry
        dut.in_complete.value = 1
        await self.usb_cycle()
        dut.in_complete.value = 0
        dut.in_complete_entry.value = self.rng.randrange(NUM_ENDPOINTS)
        await self.wait_for(lambda: self.events.outstanding == 0, 256, f"the event for IN packet from {entry}")

    async def _tx_monitor(self) -> None:
        dut = self.dut
        stalled: StreamBeat | None = None
        while True:
            await self.usb_cycle()
            ready = int(self.tx_throttle())
            dut.tx_payload_tready.value = ready
            await ReadOnly()
            try:
                valid = resolved(dut.tx_payload_tvalid.value, "tx_payload_tvalid")
                if valid:
                    data_value = dut.tx_payload_tdata.value
                    beat = StreamBeat(
                        data=int(data_value) if data_value.is_resolvable else None,
                        keep=resolved(dut.tx_payload_tkeep.value, "tx_payload_tkeep"),
                        dest=resolved(dut.tx_payload_tdest.value, "tx_payload_tdest"),
                        last=resolved(dut.tx_payload_tlast.value, "tx_payload_tlast"),
                    )
            except ValueError as error:
                self.errors.append(str(error))
                stalled = None
                continue
            if not valid:
                if stalled is not None:
                    self.errors.append(f"tx_payload_tvalid dropped before beat {stalled} was accepted")
                    stalled = None
                continue
            if stalled is not None and beats_differ(stalled, beat):
                self.errors.append(f"stalled tx beat changed from {stalled} to {beat}")
            if ready:
                self.scoreboard.observe(beat)
                stalled = None
            else:
                stalled = beat

    # ------------------------------------------------------------- events
    async def _event_monitor(self) -> None:
        dut = self.dut
        while True:
            await RisingEdge(dut.clk_sys)
            await ReadOnly()
            try:
                if not resolved(dut.event_valid.value, "event_valid"):
                    continue
                event = TransferEvent(
                    endpoint=resolved(dut.event_endpoint.value, "event_endpoint"),
                    byte_count=resolved(dut.event_byte_count.value, "event_byte_count"),
                    zlp=bool(resolved(dut.event_zlp.value, "event_zlp")),
                    overrun=bool(resolved(dut.event_overrun.value, "event_overrun")),
                    setup=bool(resolved(dut.event_setup.value, "event_setup")),
                )
            except ValueError as error:
                self.errors.append(str(error))
                continue
            self.events.observe(event)

    # ------------------------------------------------------------- report
    async def finish(self) -> None:
        await self.usb_idle(64)
        if not self.rx_idle:
            self.errors.append(f"{len(self.rx_queue)} rx beats were never consumed")
        await ReadOnly()
        armed = int(self.dut.usb_armed.value)
        expected_armed = sum(int(a) << k for k, a in enumerate(self.model.armed))
        if armed != expected_armed:
            self.errors.append(f"armed = {armed:#06x}, expected {expected_armed:#06x}")
        observed = await self.ram_read_all()
        self.errors.extend(compare_ram(self.model.ram, observed, f"{self.name}, SEED={self.seed:#x}"))
        self.scoreboard.check_complete()
        self.events.check_complete()
        errors = self.errors + self.scoreboard.errors + self.events.errors
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


async def _setup(env: EndpointsEnv, direction: Callable[[int], int],
                 enable: Callable[[int], bool] = lambda _: True) -> None:
    await env.start()
    await env.ram_preload_random()
    config, capacity = random_config(env.rng, direction, enable)
    await env.configure(config, capacity)
    await env.set_enable_core(True)


# ======================================================================= tests
@cocotb.test()
async def test_reset_state(dut) -> None:
    """After reset nothing is armed: no events, no IN beats, RAM untouched."""
    env = EndpointsEnv(dut, "reset state")
    await env.start()
    await env.ram_preload_random()
    for _ in range(64):
        await env.usb_cycle()
        await ReadOnly()
        value = dut.tx_payload_tvalid.value
        if not value.is_resolvable or int(value):
            env.errors.append(f"tx_payload_tvalid = {value} without a request")
            break
    await env.finish()


@cocotb.test()
@cocotb.parametrize(sys_clock=list(SYS_PERIODS))
async def test_out_transfers(dut, sys_clock) -> None:
    """Armed OUT endpoints receive one packet each: RAM written, event with byte count, ZLP flagged."""
    env = EndpointsEnv(dut, "OUT transfers", SYS_PERIODS[sys_clock])
    await _setup(env, lambda _: DIRECTION_OUT)
    for index in range(40):
        env.rx_throttle = Throttle(env.rng, THROTTLE_MODES[index % len(THROTTLE_MODES)])
        endpoint = env.rng.randrange(NUM_ENDPOINTS)
        await env.arm(endpoint)
        await env.out_packet(endpoint, _payload(env.rng, env.capacity[endpoint]))
    await env.finish()


@cocotb.test()
async def test_overrun(dut) -> None:
    """An OUT packet for an endpoint that is not armed is dropped and raises an overrun event."""
    env = EndpointsEnv(dut, "overrun")
    await _setup(env, lambda _: DIRECTION_OUT)
    endpoint, never_armed = env.rng.sample(range(NUM_ENDPOINTS), 2)
    data = lambda: bytes(env.rng.randrange(256) for _ in range(env.rng.randint(1, env.capacity[endpoint])))
    await env.out_packet(never_armed, data())
    await env.arm(endpoint)
    await env.out_packet(endpoint, data())
    await env.out_packet(endpoint, data())
    await env.out_packet(endpoint, b"")
    await env.arm(endpoint)
    await env.out_packet(endpoint, data())
    await env.out_packet(never_armed, b"")
    await env.finish()


@cocotb.test()
@cocotb.parametrize(sys_clock=list(SYS_PERIODS))
async def test_in_transfers(dut, sys_clock) -> None:
    """Armed IN endpoints send length bytes once per arm; requests without arm are ignored."""
    env = EndpointsEnv(dut, "IN transfers", SYS_PERIODS[sys_clock])
    await _setup(env, lambda _: DIRECTION_IN)
    for index in range(40):
        env.tx_throttle = Throttle(env.rng, THROTTLE_MODES[index % len(THROTTLE_MODES)])
        endpoint = env.rng.randrange(NUM_ENDPOINTS)
        if env.rng.random() < 0.15:
            await env.in_request(endpoint)
            continue
        await env.arm(endpoint, len(_payload(env.rng, env.capacity[endpoint])))
        await env.in_request(endpoint)
        if env.rng.random() < 0.2:
            await env.in_request(endpoint)
    await env.finish()


@cocotb.test()
async def test_buffer_size(dut) -> None:
    """Each entry owns 2 ** (size + 3) bytes. OUT data past the buffer is never written
    (usb_protocol gives such a packet no verdict, so it does not complete), and IN
    sends at most the buffer, even when transfer.length asks for more. Buffers sit
    back to back, so any byte past a buffer would land in a neighbour."""
    env = EndpointsEnv(dut, "buffer size")
    await env.start()
    await env.ram_preload_random()
    sizes = [env.rng.randrange(4) for _ in range(NUM_ENDPOINTS)]
    directions = [env.rng.choice((DIRECTION_IN, DIRECTION_OUT)) for _ in range(NUM_ENDPOINTS)]
    config = [EndpointConfig(k * SLOT_SIZE, True, directions[k], size=sizes[k]) for k in range(NUM_ENDPOINTS)]
    for k in range(0, NUM_ENDPOINTS, 2):
        config[k] = EndpointConfig(k * SLOT_SIZE + SLOT_SIZE - (8 << sizes[k]), True, directions[k], size=sizes[k])
    await env.configure(config, [SLOT_SIZE] * NUM_ENDPOINTS)
    await env.set_enable_core(True)
    for _ in range(60):
        k = env.rng.randrange(NUM_ENDPOINTS)
        limit = config[k].buffer_bytes
        length = env.rng.choice((limit, limit + 1, env.rng.randint(0, limit), env.rng.randint(limit + 1, 2 * limit + 8)))
        if directions[k] == DIRECTION_OUT:
            await env.arm(k)
            payload = bytes(env.rng.randrange(256) for _ in range(length))
            await env.out_packet(k, payload, result=length <= limit)
            if length > limit:
                await env.out_packet(k, payload[:limit])
        else:
            await env.arm(k, min(length, 1023))
            await env.in_request(k)
    await env.finish()


@cocotb.test()
async def test_inactive_endpoints(dut) -> None:
    """Disabled endpoints, wrong directions and a disabled core give no transfer and no event."""
    env = EndpointsEnv(dut, "inactive endpoints")
    kinds = [(DIRECTION_OUT, True), (DIRECTION_OUT, False), (DIRECTION_IN, True), (DIRECTION_IN, False)]
    assignment = [kinds[k % 4] for k in range(NUM_ENDPOINTS)]
    env.rng.shuffle(assignment)
    await _setup(env, lambda k: assignment[k][0], lambda k: assignment[k][1])
    for endpoint in range(NUM_ENDPOINTS):
        await env.arm(endpoint, env.rng.randint(0, 8))
    for endpoint in env.rng.sample(range(NUM_ENDPOINTS), NUM_ENDPOINTS):
        direction, enabled = assignment[endpoint]
        if direction == DIRECTION_OUT and enabled:
            continue
        await env.out_packet(endpoint, _payload(env.rng, env.capacity[endpoint]))
        if not (direction == DIRECTION_IN and enabled):
            await env.in_request(endpoint)

    # With the core disabled, even armed and enabled endpoints do nothing...
    await env.set_enable_core(False)
    for endpoint in range(NUM_ENDPOINTS):
        await env.out_packet(endpoint, _payload(env.rng, env.capacity[endpoint]))
        await env.in_request(endpoint)
    # ...and they keep their buffers armed until the core is enabled again.
    await env.set_enable_core(True)
    for endpoint in env.rng.sample(range(NUM_ENDPOINTS), NUM_ENDPOINTS):
        direction, enabled = assignment[endpoint]
        if enabled and direction == DIRECTION_OUT:
            await env.out_packet(endpoint, _payload(env.rng, env.capacity[endpoint]))
        elif enabled:
            await env.in_request(endpoint)
    await env.finish()


@cocotb.test()
async def test_arm_before_enable(dut) -> None:
    """A buffer armed while its endpoint is disabled is used once the endpoint is enabled."""
    env = EndpointsEnv(dut, "arm before enable")
    directions = [env.rng.choice((DIRECTION_IN, DIRECTION_OUT)) for _ in range(NUM_ENDPOINTS)]
    await _setup(env, lambda k: directions[k], lambda _: False)
    for endpoint in range(NUM_ENDPOINTS):
        await env.arm(endpoint, env.rng.randint(0, 16))
    config = [EndpointConfig(entry.base_address, True, entry.direction) for entry in env.model.config]
    await env.configure(config)
    for endpoint in env.rng.sample(range(NUM_ENDPOINTS), NUM_ENDPOINTS):
        if directions[endpoint] == DIRECTION_OUT:
            await env.out_packet(endpoint, _payload(env.rng, env.capacity[endpoint]))
        else:
            await env.in_request(endpoint)
    await env.finish()


@cocotb.test()
@cocotb.parametrize(sys_clock=list(SYS_PERIODS))
async def test_random_operations(dut, sys_clock) -> None:
    """Random arms, OUT packets, IN requests and reconfigurations against the model."""
    env = EndpointsEnv(dut, "random operations", SYS_PERIODS[sys_clock])
    directions = [env.rng.choice((DIRECTION_IN, DIRECTION_OUT)) for _ in range(NUM_ENDPOINTS)]
    await _setup(env, lambda k: directions[k], lambda _: env.rng.random() < 0.8)
    for _ in range(150):
        env.rx_throttle = Throttle(env.rng, env.rng.choice(THROTTLE_MODES))
        env.tx_throttle = Throttle(env.rng, env.rng.choice(THROTTLE_MODES))
        endpoint = env.rng.randrange(NUM_ENDPOINTS)
        operation = env.rng.choices(("arm", "out", "in", "reconfigure"), (5, 4, 4, 1))[0]
        if operation == "arm":
            await env.arm(endpoint, len(_payload(env.rng, env.capacity[endpoint])))
        elif operation == "out":
            await env.out_packet(endpoint, _payload(env.rng, env.capacity[endpoint]))
        elif operation == "in":
            await env.in_request(endpoint)
        else:
            # Disable everything, then write new regions, directions and enables in one cycle.
            disabled = [EndpointConfig(e.base_address, False, e.direction) for e in env.model.config]
            await env.configure(disabled)
            config, capacity = random_config(
                env.rng,
                lambda _: env.rng.choice((DIRECTION_IN, DIRECTION_OUT)),
                lambda _: env.rng.random() < 0.8,
            )
            await env.configure(config, capacity)
    await env.finish()



@cocotb.test()
async def test_foreign_packets(dut) -> None:
    """A packet whose rx_route is not accepted belongs to another device: it writes
    nothing to any buffer and raises no event, not even an overrun."""
    env = EndpointsEnv(dut, "foreign packets")
    await _setup(env, lambda _: DIRECTION_OUT)
    armed, unarmed = env.rng.sample(range(NUM_ENDPOINTS), 2)
    data = lambda entry: bytes(env.rng.randrange(256) for _ in range(env.rng.randint(1, env.capacity[entry])))
    await env.arm(armed)
    await env.out_packet(armed, data(armed), accept=False)
    await env.out_packet(unarmed, data(unarmed), accept=False)
    await env.out_packet(armed, b"", accept=False)
    await env.out_packet(armed, data(armed))
    await env.out_packet(unarmed, data(unarmed), accept=False)
    await env.finish()


@cocotb.test()
async def test_device_address_crossing(dut) -> None:
    """device_address reaches clk_usb and settles on the last value written, even
    when it changes faster than one crossing takes."""
    env = EndpointsEnv(dut, "device address")
    await env.start()
    await env.ram_preload_random()
    await env.settle()
    if int(dut.usb_device_address.value) != 0:
        env.errors.append(f"usb_device_address = {int(dut.usb_device_address.value)} after reset")
    for index in range(40):
        burst = env.rng.choice((1, 1, 2, 5))
        for _ in range(burst):
            value = env.rng.randrange(128)
            await env.sys_cycle()
            dut.sys_device_address.value = value
            for _ in range(env.rng.randrange(3)):
                await env.sys_cycle()
        await env.settle()
        await env.settle()
        seen = int(dut.usb_device_address.value)
        if seen != value:
            env.errors.append(f"step {index}: usb_device_address = {seen}, last written {value}")
    await env.finish()



@cocotb.test()
async def test_setup(dut) -> None:
    """SETUP data is held, not written, while it arrives. Only when setup_received
    reports usb_protocol's acceptance are its first 8 bytes written to the entry's
    buffer (even while it is not armed), and the SETUP event follows the write:
    software reading the buffer on the event sees the new bytes. A SETUP that is
    not accepted writes nothing; bytes past the 8th are never written. The event
    reports 8 bytes and disarms every entry serving the same endpoint number."""
    env = EndpointsEnv(dut, "SETUP")
    await env.start()
    await env.ram_preload_random()
    config, capacity = random_config(env.rng, lambda k: DIRECTION_OUT if k % 2 == 0 else DIRECTION_IN)
    numbers = [k // 2 for k in range(NUM_ENDPOINTS)]
    config = [EndpointConfig(c.base_address, c.enable, c.direction, n) for c, n in zip(config, numbers)]
    await env.configure(config, capacity)
    await env.set_enable_core(True)
    setup_bytes = lambda n=8: bytes(env.rng.randrange(256) for _ in range(n))

    out_entry, in_entry, other_in = 4, 5, 9
    await env.arm(in_entry, 3)
    await env.arm(other_in, 3)
    await env.out_packet(out_entry, setup_bytes(), setup=True)
    await env.setup_received(check_buffer=True)
    await env.in_request(in_entry)
    await env.in_request(other_in)

    await env.out_packet(out_entry, setup_bytes(), setup=True)
    await env.out_packet(out_entry, setup_bytes(7), setup=True)
    await env.out_packet(out_entry, setup_bytes(12), setup=True)
    await env.setup_received(check_buffer=True)

    await env.arm(out_entry)
    await env.out_packet(out_entry, setup_bytes(), setup=True)
    await env.setup_received(check_buffer=True)
    await env.out_packet(out_entry, bytes(range(5)))

    await env.arm(out_entry)
    await env.out_packet(out_entry, bytes(range(6)))
    await env.finish()


@cocotb.test()
async def test_control_crossing(dut) -> None:
    """stall reaches clk_usb as a level; each reset_toggle pulse arrives as one pulse."""
    env = EndpointsEnv(dut, "stall and reset_toggle crossing")
    await env.start()
    await env.ram_preload_random()
    pulses = [0] * NUM_ENDPOINTS

    async def count() -> None:
        while True:
            await RisingEdge(dut.clk_usb)
            await ReadOnly()
            value = int(dut.usb_reset_toggle.value)
            for k in range(NUM_ENDPOINTS):
                pulses[k] += value >> k & 1

    cocotb.start_soon(count())
    expected = [0] * NUM_ENDPOINTS
    for index in range(20):
        stall = env.rng.getrandbits(NUM_ENDPOINTS)
        mask = env.rng.getrandbits(NUM_ENDPOINTS)
        await env.sys_cycle()
        dut.endpoint_stall.value = stall
        dut.reset_toggle.value = mask
        await env.sys_cycle()
        dut.reset_toggle.value = 0
        for k in range(NUM_ENDPOINTS):
            expected[k] += mask >> k & 1
        await env.settle()
        await ReadOnly()
        if int(dut.usb_endpoint_stall.value) != stall:
            env.errors.append(f"step {index}: usb stall {int(dut.usb_endpoint_stall.value):#06x}, expected {stall:#06x}")
        await env.usb_cycle()
    await env.settle()
    if pulses != expected:
        env.errors.append(f"reset_toggle pulses per entry {pulses}, expected {expected}")
    await env.finish()



@cocotb.test()
async def test_in_resend(dut) -> None:
    """Without the host's ACK an IN entry stays armed and sends the same data again;
    the ACK (in_result) hands the buffer back. An isochronous entry completes at the
    end of the read, without an ACK."""
    env = EndpointsEnv(dut, "IN resend")
    await env.start()
    await env.ram_preload_random()
    config, capacity = random_config(env.rng, lambda _: DIRECTION_IN)
    config = [EndpointConfig(c.base_address, c.enable, c.direction, k, 3 if k == 7 else 1)
              for k, c in enumerate(config)]
    await env.configure(config, capacity)
    await env.set_enable_core(True)

    entry = 4
    await env.arm(entry, env.rng.randint(1, capacity[entry]))
    await env.in_request(entry, ack=False)
    await env.in_request(entry, ack=False)
    await env.in_request(entry)
    await env.in_request(entry)
    await env.arm(entry, 0)
    await env.in_request(entry, ack=False)
    await env.in_request(entry)

    await env.arm(7, env.rng.randint(1, capacity[7]))
    await env.in_request(7)
    await env.in_request(7)
    await env.finish()


@cocotb.test()
async def test_in_split(dut) -> None:
    """IN transfers split into packets of buffer >> split bytes: each request sends the
    next packet, a request without an ACK sends the same packet again, and only the
    packet that is short or ends the transfer completes it. in_last tells usb_protocol
    whether the next packet ends the transfer."""
    env = EndpointsEnv(dut, "IN split")
    await env.start()
    await env.ram_preload_random()
    config = []
    for k in range(NUM_ENDPOINTS):
        size = env.rng.randrange(1, 4)
        config.append(EndpointConfig(k * SLOT_SIZE, True, DIRECTION_IN, size=size,
                                     split=env.rng.randrange(size + 1),
                                     kind=ISOCHRONOUS if env.rng.random() < 0.15 else 0))
    await env.configure(config, [SLOT_SIZE] * NUM_ENDPOINTS)
    await env.set_enable_core(True)

    def check_last(k: int) -> None:
        observed = int(dut.usb_in_last.value) >> k & 1
        expected = int(env.model.in_last(k))
        if observed != expected:
            env.errors.append(f"in_last({k}) = {observed}, expected {expected} "
                              f"(sent {env.model.sent[k]} of {env.model.length[k]})")

    for _ in range(40):
        k = env.rng.randrange(NUM_ENDPOINTS)
        limit = config[k].buffer_bytes
        packet = config[k].packet_bytes
        length = env.rng.choice((0, packet, limit, env.rng.randint(0, limit), env.rng.randint(0, limit),
                                 limit + 5, (limit // packet - 1) * packet))
        await env.arm(k, length)
        for _ in range(limit // 8 + 3):
            if not env.model.armed[k]:
                break
            check_last(k)
            ack = env.rng.random() < 0.8
            await env.in_request(k, ack=ack)
        if env.model.armed[k]:
            env.errors.append(f"entry {k} still armed after its transfer")
    await env.finish()
