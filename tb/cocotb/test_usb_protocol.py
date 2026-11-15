"""usb_protocol: routing of DATA payloads, the SETUP response and OUT handling.

Routing: on every packet record with a good PID and CRC, an OUT or SETUP token
addressed to ``device_address`` selects the lowest enabled OUT entry serving the
token's endpoint (SETUP only a CONTROL entry). ``rx_route.accept`` is set when
the payload is to be written: always for SETUP (``rx_route.setup``), for OUT only
when the entry is armed and not stalled. Any other packet and a bus reset clear
it. ``rx_route.entry`` matters only while ``accept`` is set.

SETUP: the DATA0 record that follows, with a good PID and CRC and exactly 8
bytes, is answered with ACK and pulses ``setup_received``; both entries serving
that endpoint number then expect DATA1.

OUT: the DATA record after an OUT token is answered from the entry's state at
the token. A bad PID or CRC gets no answer, and so does data longer than the
entry's buffer (2 ** (size + 3) bytes; nothing is reported and the toggle stays). Stalled: STALL. Not armed: NAK and
``out_result.overrun``. Armed: ACK; with the expected data PID the toggle flips
and ``out_result.complete`` reports the byte count, with the other PID the data
is dropped. Isochronous entries get no handshake and no toggle check. Toggles
return to DATA0 on reset, bus reset and ``reset_toggle``.

IN: an IN token for the lowest enabled IN entry serving its endpoint is answered
from the entry's state: STALL, NAK, or DATA0/DATA1 by the toggle together with
``in_request`` (the RAM read). If the next packet is an ACK, the toggle flips and
``in_result.complete`` hands the buffer back; otherwise the entry stays armed and
the next IN sends the same data with the same PID. Isochronous IN entries send
DATA0 and expect no ACK (they complete at the end of the read, in usb_endpoints).
While ``in_busy`` (a RAM read still in progress) an armed entry is treated as not
armed: NAK, or no answer for isochronous, and no ``in_request``.

Double buffering: ``double_buffer`` on entry k (k < 15, not CONTROL) pairs it with
entry k+1, which is then never matched. A token for entry k is served from entry
``k + next[k]``: its ``armed`` and ``size``, entry k's ``stall``, ``kind`` and toggle;
routes, requests and results name the serving entry. A completed transfer flips
``next[k]`` (isochronous IN when the data is sent), for IN only when the packet ends
the transfer (``in_last`` of the serving entry, from usb_endpoints, for transfers
split into packets). ``next`` returns to 0 on reset,
bus reset and while entry k is disabled or not paired, but not on ``reset_toggle``.
"""
from __future__ import annotations

import os
import random
from dataclasses import dataclass, replace
from fractions import Fraction

import cocotb
from cocotb.triggers import ReadOnly, ReadWrite, RisingEdge

from drivers.clock_reset import ClockDomain, start_clocks_and_reset

ENTRIES = 16
CONTROL, BULK, INTERRUPT, ISOCHRONOUS = range(4)
DIRECTION_IN, DIRECTION_OUT = 0, 1
PID_OUT, PID_IN, PID_SOF, PID_SETUP = 0x1, 0x9, 0x5, 0xD
PID_DATA0, PID_DATA1, PID_ACK, PID_NAK, PID_STALL = 0x3, 0xB, 0x2, 0xA, 0xE
ALL_PIDS = (PID_OUT, PID_IN, PID_SOF, PID_SETUP, PID_DATA0, PID_DATA1, PID_ACK, PID_NAK, PID_STALL)
DATA_PIDS = (PID_DATA0, PID_DATA1)
RANDOM_PACKETS = 4000


@dataclass(frozen=True)
class Entry:
    enable: int
    direction: int
    number: int
    kind: int
    armed: int = 0
    stall: int = 0
    size: int = 7
    double_buffer: int = 0
    last: int = 1

    @property
    def buffer_bytes(self) -> int:
        return 8 << self.size


@dataclass(frozen=True)
class Packet:
    pid: int
    address: int = 0
    endpoint: int = 0
    pid_ok: int = 1
    crc_ok: int = 1
    length: int = 0


@dataclass
class Route:
    accept: int = 0
    entry: int = 0
    setup: int = 0


@dataclass(frozen=True)
class Response:
    tx: tuple[int, ...] = ()
    setup: tuple[int, ...] = ()
    complete: tuple[tuple[int, int], ...] = ()
    overrun: tuple[int, ...] = ()
    in_request: tuple[int, ...] = ()
    in_complete: tuple[int, ...] = ()


def paired(entries: list[Entry], k: int) -> bool:
    return k < ENTRIES - 1 and bool(entries[k].double_buffer) and entries[k].kind != CONTROL


@dataclass(frozen=True)
class Pending:
    entry: int
    leader: int
    paired: bool
    stall: int
    kind: int
    armed: int
    size: int


class ProtocolModel:
    def __init__(self) -> None:
        self.route = Route()
        self.pending: Pending | None = None
        self.in_pending: tuple[int, int, bool] | None = None
        self.toggle = [0] * ENTRIES
        self.next = [0] * ENTRIES

    def settle(self, entries: list[Entry]) -> None:
        for k in range(ENTRIES):
            if not (paired(entries, k) and entries[k].enable):
                self.next[k] = 0

    def _serving(self, k: int, entries: list[Entry]) -> int:
        return k + self.next[k] if paired(entries, k) else k

    def _match(self, packet: Packet, entries: list[Entry], device_address: int, enable_core: int) -> int | None:
        if packet.pid not in (PID_OUT, PID_SETUP, PID_IN) or not enable_core or packet.address != device_address:
            return None
        direction = DIRECTION_IN if packet.pid == PID_IN else DIRECTION_OUT
        for index, entry in enumerate(entries):
            if index > 0 and paired(entries, index - 1):
                continue
            if (entry.enable and entry.direction == direction and entry.number == packet.endpoint
                    and (packet.pid != PID_SETUP or entry.kind == CONTROL)):
                return index
        return None

    def _in(self, k: int, entries: list[Entry], in_busy: int) -> Response:
        leader = entries[k]
        s = self._serving(k, entries)
        iso = leader.kind == ISOCHRONOUS
        if leader.stall:
            return Response() if iso else Response(tx=(PID_STALL,))
        if not entries[s].armed or in_busy:
            return Response() if iso else Response(tx=(PID_NAK,))
        flip = paired(entries, k) and bool(entries[s].last)
        if iso:
            if flip:
                self.next[k] ^= 1
            return Response(tx=(PID_DATA0,), in_request=(s,))
        self.in_pending = (s, k, flip)
        return Response(tx=(DATA_PIDS[self.toggle[k]],), in_request=(s,))

    def _data(self, packet: Packet, entries: list[Entry]) -> Response:
        if self.route.accept and self.route.setup:
            if packet.pid == PID_DATA0 and packet.length == 8:
                number = entries[self.route.entry].number
                for index, entry in enumerate(entries):
                    if entry.number == number:
                        self.toggle[index] = 1
                return Response(tx=(PID_ACK,), setup=(self.route.entry,))
            return Response()
        if self.pending is None:
            return Response()
        p = self.pending
        if packet.length > 8 << p.size:
            return Response()
        iso = p.kind == ISOCHRONOUS
        if p.stall:
            return Response() if iso else Response(tx=(PID_STALL,))
        if not p.armed:
            return Response(overrun=(p.entry,)) if iso else Response(tx=(PID_NAK,), overrun=(p.entry,))
        if iso:
            if p.paired:
                self.next[p.leader] ^= 1
            return Response(complete=((p.entry, packet.length),))
        if packet.pid == DATA_PIDS[self.toggle[p.leader]]:
            self.toggle[p.leader] ^= 1
            if p.paired:
                self.next[p.leader] ^= 1
            return Response(tx=(PID_ACK,), complete=((p.entry, packet.length),))
        return Response(tx=(PID_ACK,))

    def packet(self, packet: Packet, entries: list[Entry], device_address: int, enable_core: int,
               in_busy: int = 0) -> Response:
        response = self._packet(packet, entries, device_address, enable_core, in_busy)
        self.settle(entries)
        return response

    def _packet(self, packet: Packet, entries: list[Entry], device_address: int, enable_core: int,
                in_busy: int) -> Response:
        response = Response()
        if self.in_pending is not None:
            if packet.pid == PID_ACK and packet.pid_ok:
                s, k, was_paired = self.in_pending
                self.toggle[k] ^= 1
                if was_paired:
                    self.next[k] ^= 1
                response = Response(in_complete=(s,))
            self.in_pending = None
        if packet.pid_ok and packet.crc_ok and packet.pid in DATA_PIDS:
            response = self._data(packet, entries)
        self.pending = None
        self.route.accept = 0
        if not (packet.pid_ok and packet.crc_ok):
            return response
        k = self._match(packet, entries, device_address, enable_core)
        if k is None:
            return response
        if packet.pid == PID_IN:
            return self._in(k, entries, in_busy)
        if packet.pid == PID_SETUP:
            self.route = Route(1, k, 1)
        else:
            s = self._serving(k, entries)
            leader = entries[k]
            self.pending = Pending(s, k, paired(entries, k), leader.stall, leader.kind, entries[s].armed,
                                   entries[s].size)
            if entries[s].armed and not leader.stall:
                self.route = Route(1, s, 0)
        return response

    def reset(self) -> None:
        self.route.accept = 0
        self.pending = None
        self.in_pending = None
        self.toggle = [0] * ENTRIES
        self.next = [0] * ENTRIES

    def reset_toggle(self, mask: int) -> None:
        for index in range(ENTRIES):
            if mask >> index & 1:
                self.toggle[index] = 0


class Bench:
    def __init__(self, dut) -> None:
        self.dut = dut
        self.model = ProtocolModel()
        self.entries = [Entry(0, DIRECTION_IN, 0, CONTROL)] * ENTRIES
        self.device_address = 0
        self.enable_core = 1
        self.in_busy = 0
        self.seen = Response()

    async def _pulse_monitor(self) -> None:
        dut = self.dut
        while True:
            await RisingEdge(dut.clk)
            await ReadOnly()
            seen = self.seen
            if int(dut.tx_valid.value):
                seen = replace(seen, tx=seen.tx + (int(dut.tx_pid.value),))
            if int(dut.setup_received.value):
                seen = replace(seen, setup=seen.setup + (int(dut.route_entry.value),))
            if int(dut.out_complete.value):
                seen = replace(seen, complete=seen.complete + ((int(dut.out_entry.value), int(dut.out_byte_count.value)),))
            if int(dut.out_overrun.value):
                seen = replace(seen, overrun=seen.overrun + (int(dut.out_entry.value),))
            if int(dut.in_request_start.value):
                seen = replace(seen, in_request=seen.in_request + (int(dut.in_request_entry.value),))
            if int(dut.in_complete.value):
                seen = replace(seen, in_complete=seen.in_complete + (int(dut.in_complete_entry.value),))
            self.seen = seen

    async def start(self) -> None:
        dut = self.dut
        for name in ("packet_valid", "packet_pid", "packet_address", "packet_endpoint", "packet_length",
                     "reset_detected", "device_address", "endpoint_enable", "endpoint_direction",
                     "endpoint_number", "endpoint_kind", "endpoint_armed", "endpoint_stall", "reset_toggle",
                     "in_busy", "endpoint_double_buffer", "endpoint_in_last"):
            getattr(dut, name).value = 0
        dut.packet_pid_ok.value = 1
        dut.packet_crc_ok.value = 1
        dut.enable_core.value = 1
        dut.endpoint_size.value = (1 << (3 * ENTRIES)) - 1
        await start_clocks_and_reset(ClockDomain(dut.clk, dut.rst_n, Fraction(20833, 1000)))
        cocotb.start_soon(self._pulse_monitor())
        await self.check("after reset")

    async def configure(self, entries: list[Entry], device_address: int | None = None,
                        enable_core: int | None = None, in_busy: int | None = None) -> None:
        self.entries = list(entries)
        if in_busy is not None:
            self.in_busy = in_busy
        if device_address is not None:
            self.device_address = device_address
        if enable_core is not None:
            self.enable_core = enable_core
        dut = self.dut
        await RisingEdge(dut.clk)
        await ReadWrite()
        dut.endpoint_enable.value = sum(e.enable << i for i, e in enumerate(entries))
        dut.endpoint_direction.value = sum(e.direction << i for i, e in enumerate(entries))
        dut.endpoint_number.value = sum(e.number << (4 * i) for i, e in enumerate(entries))
        dut.endpoint_kind.value = sum(e.kind << (2 * i) for i, e in enumerate(entries))
        dut.endpoint_armed.value = sum(e.armed << i for i, e in enumerate(entries))
        dut.endpoint_stall.value = sum(e.stall << i for i, e in enumerate(entries))
        dut.endpoint_size.value = sum(e.size << (3 * i) for i, e in enumerate(entries))
        dut.endpoint_double_buffer.value = sum(e.double_buffer << i for i, e in enumerate(entries))
        dut.endpoint_in_last.value = sum(e.last << i for i, e in enumerate(entries))
        self.model.settle(self.entries)
        dut.device_address.value = self.device_address
        dut.enable_core.value = self.enable_core
        dut.in_busy.value = self.in_busy

    async def send(self, packet: Packet, context: str) -> None:
        dut = self.dut
        await RisingEdge(dut.clk)
        await ReadWrite()
        dut.packet_pid.value = packet.pid
        dut.packet_address.value = packet.address
        dut.packet_endpoint.value = packet.endpoint
        dut.packet_pid_ok.value = packet.pid_ok
        dut.packet_crc_ok.value = packet.crc_ok
        dut.packet_length.value = packet.length
        dut.packet_valid.value = 1
        self.seen = Response()
        await RisingEdge(dut.clk)
        await ReadWrite()
        dut.packet_valid.value = 0
        expected = self.model.packet(packet, self.entries, self.device_address, self.enable_core, self.in_busy)
        await self.check(f"{context}: {packet}")
        assert self.seen == expected, f"{context}: {packet}: observed {self.seen}, expected {expected}"

    async def _pulse(self, name: str, value: int) -> None:
        dut = self.dut
        await RisingEdge(dut.clk)
        await ReadWrite()
        getattr(dut, name).value = value
        await RisingEdge(dut.clk)
        await ReadWrite()
        getattr(dut, name).value = 0

    async def bus_reset(self, context: str) -> None:
        self.seen = Response()
        await self._pulse("reset_detected", 1)
        self.model.reset()
        await self.check(f"{context}: bus reset")
        assert self.seen == Response(), f"{context}: answered a bus reset with {self.seen}"

    async def reset_toggle(self, mask: int, context: str) -> None:
        await self._pulse("reset_toggle", mask)
        self.model.reset_toggle(mask)
        await self.check(f"{context}: reset_toggle {mask:#06x}")

    async def check(self, context: str) -> None:
        await RisingEdge(self.dut.clk)
        await ReadOnly()
        accept = int(self.dut.route_accept.value)
        expected = self.model.route
        assert accept == expected.accept, f"{context}: accept={accept}, expected {expected.accept}"
        if accept:
            entry = int(self.dut.route_entry.value)
            setup = int(self.dut.route_setup.value)
            assert entry == expected.entry, f"{context}: entry={entry}, expected {expected.entry}"
            assert setup == expected.setup, f"{context}: setup={setup}, expected {expected.setup}"


def _seed() -> int:
    value = os.environ.get("SEED")
    return int(value, 0) if value is not None else random.SystemRandom().getrandbits(32)


def _ep0_and_ep1(armed: int = 1) -> list[Entry]:
    entries = [Entry(0, DIRECTION_IN, 0, CONTROL)] * ENTRIES
    entries[0] = Entry(1, DIRECTION_OUT, 0, CONTROL, armed)
    entries[1] = Entry(1, DIRECTION_IN, 0, CONTROL, armed)
    entries[6] = Entry(1, DIRECTION_OUT, 1, BULK, armed)
    entries[9] = Entry(1, DIRECTION_IN, 1, BULK, armed)
    return entries


@cocotb.test()
async def test_routing(dut) -> None:
    bench = Bench(dut)
    await bench.start()
    await bench.configure(_ep0_and_ep1(), device_address=5)

    await bench.send(Packet(PID_SETUP, 5, 0), "SETUP to EP0 selects entry 0")
    await bench.send(Packet(PID_SOF), "SOF clears the route")
    await bench.send(Packet(PID_OUT, 5, 1), "OUT to EP1 selects entry 6, not the IN entry 9")
    await bench.send(Packet(PID_OUT, 6, 1), "a token for another address clears the route")
    await bench.send(Packet(PID_OUT, 5, 1), "OUT to EP1 again")
    await bench.send(Packet(PID_IN, 5, 1), "an IN token clears the route")
    await bench.send(Packet(PID_SETUP, 5, 1), "SETUP to a BULK endpoint is not accepted")
    await bench.send(Packet(PID_OUT, 5, 2), "an endpoint without an entry is not accepted")
    await bench.send(Packet(PID_OUT, 5, 0, crc_ok=0), "a token with a bad CRC is not accepted")
    await bench.send(Packet(PID_OUT, 5, 0, pid_ok=0), "a token with a bad PID is not accepted")
    await bench.send(Packet(PID_OUT, 5, 0), "OUT to EP0")
    await bench.bus_reset("a bus reset clears the route")

    await bench.configure(_ep0_and_ep1(), enable_core=0)
    await bench.send(Packet(PID_OUT, 5, 0), "nothing is accepted while the core is disabled")

    duplicate = _ep0_and_ep1()
    duplicate[3] = Entry(1, DIRECTION_OUT, 1, BULK, 1)
    await bench.configure(duplicate, device_address=0, enable_core=1)
    await bench.send(Packet(PID_OUT, 0, 1), "with two matching entries the lowest one wins")


@cocotb.test()
async def test_setup_response(dut) -> None:
    bench = Bench(dut)
    await bench.start()
    await bench.configure(_ep0_and_ep1(armed=0), device_address=5)

    async def setup_then(data: Packet, context: str) -> None:
        await bench.send(Packet(PID_SETUP, 5, 0), f"{context}: SETUP token")
        await bench.send(data, context)

    await setup_then(Packet(PID_DATA0, length=8), "a good SETUP is ACKed although EP0 is not armed")
    await setup_then(Packet(PID_DATA0, length=8, crc_ok=0), "a bad CRC is not answered")
    await setup_then(Packet(PID_DATA0, length=8, pid_ok=0), "a bad PID is not answered")
    await setup_then(Packet(PID_DATA1, length=8), "SETUP data must be DATA0")
    await setup_then(Packet(PID_DATA0, length=7), "SETUP data must be 8 bytes")
    await setup_then(Packet(PID_DATA0, length=9), "SETUP data must be 8 bytes, not more")
    await bench.send(Packet(PID_DATA0, length=8), "DATA without a token is not answered")
    await bench.send(Packet(PID_SETUP, 6, 0), "SETUP to another address")
    await bench.send(Packet(PID_DATA0, length=8), "its data is not answered")
    await bench.send(Packet(PID_SETUP, 5, 0), "SETUP token")
    await bench.bus_reset("a bus reset between token and data")
    await bench.send(Packet(PID_DATA0, length=8), "data after the reset is not answered")

    stalled = _ep0_and_ep1()
    stalled[0] = replace(stalled[0], stall=1)
    await bench.configure(stalled)
    await setup_then(Packet(PID_DATA0, length=8), "a SETUP is ACKed although EP0 is stalled")


@cocotb.test()
async def test_out(dut) -> None:
    bench = Bench(dut)
    await bench.start()
    entries = _ep0_and_ep1()
    await bench.configure(entries, device_address=5)

    async def out(data: Packet, context: str, endpoint: int = 1) -> None:
        await bench.send(Packet(PID_OUT, 5, endpoint), f"{context}: OUT token")
        await bench.send(data, context)

    await out(Packet(PID_DATA0, length=12), "DATA0 is ACKed and completes")
    await out(Packet(PID_DATA0, length=12), "the old DATA0 again is ACKed and dropped")
    await out(Packet(PID_DATA1, length=0), "a zero-length DATA1 completes")
    await out(Packet(PID_DATA0, length=5, crc_ok=0), "a bad CRC is not answered and does not flip the toggle")
    await out(Packet(PID_DATA0, length=5), "DATA0 completes after the bad packet")

    entries[6] = replace(entries[6], armed=0)
    await bench.configure(entries)
    await out(Packet(PID_DATA1, length=5), "an unarmed entry answers NAK and reports an overrun")
    entries[6] = replace(entries[6], armed=1, stall=1)
    await bench.configure(entries)
    await out(Packet(PID_DATA1, length=5), "a stalled entry answers STALL")
    entries[6] = replace(entries[6], stall=0)
    await bench.configure(entries)
    await out(Packet(PID_DATA1, length=5), "the toggle did not move while NAKing and STALLing")
    await bench.reset_toggle(1 << 6, "reset_toggle returns EP1 OUT to DATA0")
    await out(Packet(PID_DATA0, length=3), "DATA0 completes after reset_toggle")

    await bench.send(Packet(PID_SETUP, 5, 0), "SETUP token")
    await bench.send(Packet(PID_DATA0, length=8), "SETUP data")
    await out(Packet(PID_DATA0, length=2), "after SETUP, EP0 OUT drops DATA0", endpoint=0)
    await out(Packet(PID_DATA1, length=2), "and accepts DATA1", endpoint=0)

    await bench.bus_reset("a bus reset returns every toggle to DATA0")
    await out(Packet(PID_DATA0, length=4), "EP1 OUT accepts DATA0 after the bus reset")

    await bench.send(Packet(PID_OUT, 5, 1), "OUT token")
    await bench.send(Packet(PID_IN, 5, 1), "an IN token in between")
    await bench.send(Packet(PID_DATA1, length=4), "data no longer follows an OUT token: no answer")

    entries[6] = replace(entries[6], kind=ISOCHRONOUS)
    await bench.configure(entries)
    await out(Packet(PID_DATA1, length=9), "isochronous: completes without a handshake")
    await out(Packet(PID_DATA1, length=9), "isochronous: no toggle check")
    entries[6] = replace(entries[6], armed=0)
    await bench.configure(entries)
    await out(Packet(PID_DATA0, length=9), "isochronous and not armed: overrun without NAK")


@cocotb.test()
async def test_out_buffer_size(dut) -> None:
    """OUT data longer than the entry's buffer (2 ** (size + 3) bytes) is a packet error:
    no handshake, no completion or overrun, the toggle stays (USB 2.0 sections 8.5.2, 8.6.4)."""
    bench = Bench(dut)
    await bench.start()
    entries = _ep0_and_ep1()
    entries[6] = replace(entries[6], size=0)
    await bench.configure(entries, device_address=5)

    async def out(data: Packet, context: str) -> None:
        await bench.send(Packet(PID_OUT, 5, 1), f"{context}: OUT token")
        await bench.send(data, context)

    await out(Packet(PID_DATA0, length=8), "8 bytes fill an 8-byte buffer: ACK and complete")
    await out(Packet(PID_DATA1, length=9), "9 bytes overflow it: no answer")
    await out(Packet(PID_DATA1, length=8), "the toggle did not move: DATA1 completes")
    for size in range(1, 8):
        entries[6] = replace(entries[6], size=size)
        await bench.configure(entries)
        limit = 8 << size
        if limit <= 1023:
            await out(Packet(PID_DATA0, length=limit + 1), f"size {size}: {limit + 1} bytes get no answer")
        await out(Packet(PID_DATA0, length=min(limit, 1023)), f"size {size}: {min(limit, 1023)} bytes complete")
        await bench.reset_toggle(1 << 6, f"size {size}")

    entries[6] = replace(entries[6], size=0, armed=0)
    await bench.configure(entries)
    await out(Packet(PID_DATA0, length=9), "not armed and too long: no NAK, no overrun")
    entries[6] = replace(entries[6], armed=1, stall=1)
    await bench.configure(entries)
    await out(Packet(PID_DATA0, length=9), "stalled and too long: no STALL")
    entries[6] = replace(entries[6], stall=0, kind=ISOCHRONOUS)
    await bench.configure(entries)
    await out(Packet(PID_DATA0, length=9), "isochronous and too long: does not complete")
    await out(Packet(PID_DATA0, length=8), "isochronous within the buffer completes")

    entries[0] = replace(entries[0], size=0)
    await bench.configure(entries)
    await bench.send(Packet(PID_SETUP, 5, 0), "SETUP token to an 8-byte EP0 buffer")
    await bench.send(Packet(PID_DATA0, length=8), "SETUP data of 8 bytes is ACKed")


@cocotb.test()
async def test_random(dut) -> None:
    seed = _seed()
    dut._log.info("SEED=%d", seed)
    rng = random.Random(seed)
    bench = Bench(dut)
    await bench.start()

    for index in range(RANDOM_PACKETS):
        if index % 200 == 0:
            entries = [Entry(int(rng.random() < 0.7), rng.choice((DIRECTION_IN, DIRECTION_OUT)),
                             rng.randrange(4), rng.randrange(4), int(rng.random() < 0.7), int(rng.random() < 0.15),
                             rng.randrange(8), int(rng.random() < 0.3), int(rng.random() < 0.7))
                       for _ in range(ENTRIES)]
            await bench.configure(entries, rng.randrange(128), int(rng.random() < 0.95), int(rng.random() < 0.2))
        context = f"seed={seed} index={index}"
        roll = rng.random()
        if roll < 0.01:
            await bench.bus_reset(context)
            continue
        if roll < 0.02:
            await bench.reset_toggle(rng.getrandbits(ENTRIES), context)
            continue
        if bench.model.route.accept or bench.model.pending is not None:
            pid = rng.choice(DATA_PIDS + DATA_PIDS + ALL_PIDS)
        else:
            pid = rng.choice(ALL_PIDS + (PID_OUT, PID_OUT, PID_SETUP, PID_IN, PID_IN, PID_ACK))
        packet = Packet(
            pid,
            bench.device_address if rng.random() < 0.85 else rng.randrange(128),
            rng.randrange(4),
            int(rng.random() < 0.97),
            int(rng.random() < 0.97),
            rng.choice((8, 8, 0, 7, 9, rng.randrange(65))),
        )
        await bench.send(packet, context)



@cocotb.test()
async def test_in(dut) -> None:
    bench = Bench(dut)
    await bench.start()
    entries = _ep0_and_ep1()
    await bench.configure(entries, device_address=5)

    async def in_token(context: str, endpoint: int = 1) -> None:
        await bench.send(Packet(PID_IN, 5, endpoint), context)

    await in_token("an armed IN entry sends DATA0 and starts the read")
    await bench.send(Packet(PID_ACK), "the ACK completes it and flips the toggle")
    await in_token("the next IN sends DATA1")
    await bench.send(Packet(PID_IN, 5, 1), "no ACK, the host retries: DATA1 again, same buffer")
    await bench.send(Packet(PID_NAK), "a NAK from the host is not an ACK")
    await in_token("still DATA1")
    await bench.send(Packet(PID_ACK, pid_ok=0), "a damaged ACK is not an ACK")
    await in_token("still DATA1")
    await bench.send(Packet(PID_ACK), "ACK: complete")
    await bench.send(Packet(PID_ACK), "an ACK with nothing outstanding does nothing")
    await in_token("DATA0 again")
    await bench.send(Packet(PID_SOF), "an SOF instead of the ACK")
    await bench.send(Packet(PID_ACK), "a late ACK does not count")
    await in_token("still DATA0")
    await bench.bus_reset("a bus reset while waiting for the ACK")
    await bench.send(Packet(PID_ACK), "no ACK is outstanding after the reset")
    await in_token("DATA0 after the bus reset")
    await bench.send(Packet(PID_ACK), "ACK")
    await bench.reset_toggle(1 << 9, "reset_toggle returns EP1 IN to DATA0")
    await in_token("DATA0 after reset_toggle")
    await bench.send(Packet(PID_ACK), "ACK")
    await bench.send(Packet(PID_IN, 5, 1, crc_ok=0), "an IN token with a bad CRC is not answered")
    await bench.send(Packet(PID_IN, 6, 1), "an IN token for another address is not answered")
    await bench.send(Packet(PID_IN, 5, 2), "an IN token for an endpoint without an entry is not answered")

    entries[9] = replace(entries[9], armed=0)
    await bench.configure(entries)
    await in_token("an unarmed IN entry answers NAK")
    entries[9] = replace(entries[9], armed=1, stall=1)
    await bench.configure(entries)
    await in_token("a stalled IN entry answers STALL")

    await bench.send(Packet(PID_SETUP, 5, 0), "SETUP token")
    await bench.send(Packet(PID_DATA0, length=8), "SETUP data")
    await in_token("after SETUP, EP0 IN sends DATA1", endpoint=0)
    await bench.send(Packet(PID_ACK), "ACK")

    entries[9] = replace(entries[9], stall=0, kind=ISOCHRONOUS)
    await bench.configure(entries)
    await in_token("isochronous: DATA0 and the read, no ACK expected")
    await bench.send(Packet(PID_ACK), "an ACK after isochronous data does nothing")
    await in_token("isochronous: DATA0 again")


@cocotb.test()
async def test_in_busy(dut) -> None:
    bench = Bench(dut)
    await bench.start()
    entries = _ep0_and_ep1()
    await bench.configure(entries, device_address=5)

    async def in_token(context: str) -> None:
        await bench.send(Packet(PID_IN, 5, 1), context)

    await bench.configure(entries, in_busy=1)
    await in_token("armed but the RAM read is busy: NAK, no in_request")
    await bench.send(Packet(PID_ACK), "an ACK after the NAK does nothing")
    await bench.configure(entries, in_busy=0)
    await in_token("not busy: DATA0 and the read, the toggle did not move")
    await bench.configure(entries, in_busy=1)
    await bench.send(Packet(PID_IN, 5, 1), "the host retries while busy: NAK")
    await bench.send(Packet(PID_ACK), "that NAK cleared the outstanding DATA0, the ACK does not count")
    await bench.configure(entries, in_busy=0)
    await in_token("still DATA0")
    await bench.send(Packet(PID_ACK), "ACK: complete")
    await in_token("DATA1")

    entries[9] = replace(entries[9], stall=1)
    await bench.configure(entries, in_busy=1)
    await in_token("STALL wins over busy")
    entries[9] = replace(entries[9], stall=0, kind=ISOCHRONOUS)
    await bench.configure(entries, in_busy=1)
    await in_token("isochronous and busy: no answer, no in_request")
    await bench.configure(entries, in_busy=0)
    await in_token("isochronous, not busy: DATA0 and the read")


def _pairs(armed: tuple[int, int, int, int] = (1, 1, 1, 1)) -> list[Entry]:
    """EP0, EP1 OUT on entries 3 (double_buffer) and 4, EP1 IN on entries 5 (double_buffer) and 6."""
    entries = _ep0_and_ep1()
    entries[6] = Entry(0, DIRECTION_IN, 0, CONTROL)
    entries[9] = Entry(0, DIRECTION_IN, 0, CONTROL)
    entries[3] = Entry(1, DIRECTION_OUT, 1, BULK, armed[0], double_buffer=1)
    entries[4] = Entry(1, DIRECTION_OUT, 1, BULK, armed[1])
    entries[5] = Entry(1, DIRECTION_IN, 1, BULK, armed[2], double_buffer=1)
    entries[6] = Entry(1, DIRECTION_IN, 1, BULK, armed[3])
    return entries


@cocotb.test()
async def test_double_buffer_out(dut) -> None:
    bench = Bench(dut)
    await bench.start()
    entries = _pairs()
    await bench.configure(entries, device_address=5)

    async def out(data: Packet, context: str) -> None:
        await bench.send(Packet(PID_OUT, 5, 1), f"{context}: OUT token")
        await bench.send(data, context)

    def arm(k: int, value: int = 1) -> None:
        entries[k] = replace(entries[k], armed=value)

    await out(Packet(PID_DATA0, length=10), "the first packet goes to entry 3")
    arm(3, 0)
    await bench.configure(entries)
    await out(Packet(PID_DATA1, length=11), "the next one to entry 4, with entry 3's toggle")
    arm(4, 0)
    await bench.configure(entries)
    await out(Packet(PID_DATA0, length=12), "entry 3 not armed: NAK and overrun on 3, never on to the armed 4")
    arm(4)
    await bench.configure(entries)
    await out(Packet(PID_DATA0, length=12), "still entry 3's turn although 4 is armed")
    arm(3)
    await bench.configure(entries)
    await out(Packet(PID_DATA0, length=13), "entry 3 armed again: completes")
    await out(Packet(PID_DATA0, length=13), "the repeated DATA0 is ACKed and dropped: no flip")
    await out(Packet(PID_DATA1, length=14), "entry 4 completes")
    await out(Packet(PID_DATA0, length=5, crc_ok=0), "a bad CRC does not flip")
    await out(Packet(PID_DATA0, length=5), "entry 3")

    await bench.reset_toggle(1 << 3, "reset_toggle on entry 3")
    await out(Packet(PID_DATA0, length=6), "DATA0 again, but still entry 4's turn")
    await bench.reset_toggle(1 << 4, "reset_toggle on entry 4 is ignored")
    await out(Packet(PID_DATA1, length=6), "entry 3 takes DATA1")

    entries[4] = replace(entries[4], stall=1)
    await bench.configure(entries)
    await out(Packet(PID_DATA0, length=7), "entry 4's stall is ignored")
    entries[4] = replace(entries[4], stall=0)
    entries[3] = replace(entries[3], stall=1)
    await bench.configure(entries)
    await out(Packet(PID_DATA1, length=7), "entry 3's stall halts the pair")
    entries[3] = replace(entries[3], stall=0)
    await bench.configure(entries)
    await out(Packet(PID_DATA1, length=7), "the stall did not flip: entry 3 completes")

    entries[4] = replace(entries[4], size=0)
    await bench.configure(entries)
    await out(Packet(PID_DATA0, length=9), "entry 4's own size: 9 bytes get no answer")
    await out(Packet(PID_DATA0, length=8), "8 bytes fit entry 4")
    entries[4] = replace(entries[4], size=7)
    await bench.configure(entries)
    await out(Packet(PID_DATA1, length=4), "entry 3; entry 4 is next")

    entries[3] = replace(entries[3], enable=0)
    await bench.configure(entries)
    await out(Packet(PID_DATA0, length=4), "entry 4 is never matched, even with entry 3 disabled")
    entries[3] = replace(entries[3], enable=1)
    await bench.configure(entries)
    await out(Packet(PID_DATA0, length=4), "disabling entry 3 returned the pair to entry 3")
    await bench.bus_reset("a bus reset with entry 4 next")
    await out(Packet(PID_DATA0, length=4), "after the bus reset: entry 3, DATA0")
    entries[3] = replace(entries[3], double_buffer=0)
    await bench.configure(entries)
    await out(Packet(PID_DATA1, length=4), "not paired: entry 3 only")
    await out(Packet(PID_DATA0, length=4), "entry 3 again")
    entries[3] = replace(entries[3], double_buffer=1, kind=ISOCHRONOUS)
    entries[4] = replace(entries[4], kind=ISOCHRONOUS)
    await bench.configure(entries)
    await out(Packet(PID_DATA1, length=3), "isochronous pair: entry 3")
    await out(Packet(PID_DATA1, length=3), "isochronous pair: entry 4")
    arm(3, 0)
    await bench.configure(entries)
    await out(Packet(PID_DATA1, length=3), "isochronous, entry 3 not armed: overrun, no flip")
    await out(Packet(PID_DATA1, length=3), "still entry 3")


@cocotb.test()
async def test_double_buffer_in(dut) -> None:
    bench = Bench(dut)
    await bench.start()
    entries = _pairs()
    await bench.configure(entries, device_address=5)

    async def in_token(context: str) -> None:
        await bench.send(Packet(PID_IN, 5, 1), context)

    await in_token("the first IN reads entry 5, DATA0")
    await bench.send(Packet(PID_ACK), "ACK completes entry 5")
    await in_token("the next IN reads entry 6, DATA1")
    await in_token("no ACK: the host retries entry 6, DATA1")
    await bench.send(Packet(PID_ACK), "ACK completes entry 6")
    entries[5] = replace(entries[5], armed=0)
    await bench.configure(entries)
    await in_token("entry 5 not armed: NAK, never on to entry 6")
    await bench.send(Packet(PID_ACK), "an ACK after the NAK does nothing")
    entries[5] = replace(entries[5], armed=1)
    await bench.configure(entries, in_busy=1)
    await in_token("busy: NAK")
    await bench.configure(entries, in_busy=0)
    await in_token("entry 5, DATA0")
    await bench.send(Packet(PID_ACK), "ACK")
    await bench.reset_toggle(1 << 5, "reset_toggle on entry 5")
    await in_token("entry 6 with DATA0")
    await bench.bus_reset("a bus reset while waiting for the ACK")
    await in_token("entry 5 after the bus reset")
    await bench.send(Packet(PID_ACK), "ACK")

    entries[5] = replace(entries[5], kind=ISOCHRONOUS)
    entries[6] = replace(entries[6], kind=ISOCHRONOUS)
    await bench.configure(entries)
    await in_token("isochronous pair: entry 6 (the flip happened on the ACK before)")
    await in_token("isochronous pair: entry 5")
    await in_token("isochronous pair: entry 6")


@cocotb.test()
async def test_double_buffer_ignored(dut) -> None:
    bench = Bench(dut)
    await bench.start()
    entries = _ep0_and_ep1()
    entries[0] = replace(entries[0], double_buffer=1)
    entries[15] = Entry(1, DIRECTION_OUT, 2, BULK, 1, double_buffer=1)
    await bench.configure(entries, device_address=5)

    await bench.send(Packet(PID_OUT, 5, 2), "OUT to entry 15")
    await bench.send(Packet(PID_DATA0, length=2), "entry 15 has no partner: it completes itself")
    await bench.send(Packet(PID_OUT, 5, 2), "OUT to entry 15")
    await bench.send(Packet(PID_DATA1, length=2), "entry 15 again")
    await bench.send(Packet(PID_IN, 5, 0), "a CONTROL entry 0 with double_buffer does not pair entry 1")
    await bench.send(Packet(PID_ACK), "ACK")
    await bench.send(Packet(PID_IN, 5, 0), "entry 1 again")


@cocotb.test()
async def test_double_buffer_in_split(dut) -> None:
    """A transfer split into packets keeps its buffer until its last packet: an ACK flips
    next_buffer only while in_last of the serving entry is set."""
    bench = Bench(dut)
    await bench.start()
    entries = _pairs()
    entries[5] = replace(entries[5], last=0)
    await bench.configure(entries, device_address=5)

    async def in_token(context: str) -> None:
        await bench.send(Packet(PID_IN, 5, 1), context)

    await in_token("entry 5, first packet of its transfer")
    await bench.send(Packet(PID_ACK), "ACK: more packets follow, no flip")
    await in_token("entry 5 again, DATA1")
    await bench.send(Packet(PID_ACK), "ACK")
    entries[5] = replace(entries[5], last=1)
    await bench.configure(entries)
    await in_token("entry 5, its last packet")
    await bench.send(Packet(PID_ACK), "ACK ends the transfer: flip")
    entries[6] = replace(entries[6], last=0)
    await bench.configure(entries)
    await in_token("entry 6, first packet")
    await bench.send(Packet(PID_ACK), "ACK, no flip")
    await in_token("entry 6 again")
    entries[5] = replace(entries[5], kind=ISOCHRONOUS)
    entries[6] = replace(entries[6], kind=ISOCHRONOUS)
    await bench.configure(entries)
    await in_token("isochronous entry 6, not its last packet: no flip")
    entries[6] = replace(entries[6], last=1)
    await bench.configure(entries)
    await in_token("isochronous entry 6, last packet: flip")
    await in_token("isochronous entry 5")
