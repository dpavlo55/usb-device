"""Turnaround of the RX and TX chains (USB 2.0 7.1.18.1).

The harness loops the parser's packet record back to the generator through a
one-cycle registered responder, the stand-in for the usb_protocol decision:
host DATA is answered with ACK, an IN token with DATA1 whose payload arrives
``payload_latency`` cycles later (a RAM read). The IN turnaround must not
depend on that latency: the generator sends SYNC and PID while the read runs. The turnaround is measured on the
bus, from the SE0-to-J transition ending the host's EOP to the first state the
device drives (the K starting its SYNC). A full-speed device must answer within
6.5 bit times and no sooner than 2 bit times.
"""
from __future__ import annotations

import os
import random
from dataclasses import dataclass, field
from fractions import Fraction

import cocotb
from cocotb.triggers import Edge, First, RisingEdge, Timer
from cocotb.utils import get_sim_time

from drivers.clock_reset import ClockDomain, start_clocks_and_reset
from drivers.usb_fs_bus import CocotbUsbFsPort, UsbFsBus
from drivers.usb_fs_host import UsbFsHost
from models.usb_fs_timing import FS_BIT_PERIOD_FS
from models.usb_fs_types import Pid

CLOCK_PERIOD_NS = Fraction(20833, 1000)
MIN_TURNAROUND_BITS = 2.0
MAX_TURNAROUND_BITS = 6.5
TRANSACTIONS_PER_CASE = 40
IN_LENGTHS = (0, 1, 8, 64)
OUT_LENGTHS = (0, 1, 8, 64)
PAYLOAD_LATENCIES = (2, 40)
CLOCK_PERIOD_FS = int(CLOCK_PERIOD_NS * 1_000_000)


@dataclass
class Turnarounds:
    bits: dict[str, list[float]] = field(default_factory=dict)
    rx_clocks: dict[str, list[float]] = field(default_factory=dict)
    tx_clocks: dict[str, list[float]] = field(default_factory=dict)

    def add(self, case: str, value: float, probe: TurnaroundProbe) -> None:
        self.bits.setdefault(case, []).append(value)
        self.rx_clocks.setdefault(case, []).append(probe.rx_clocks())
        self.tx_clocks.setdefault(case, []).append(probe.tx_clocks())

    def report(self) -> str:
        cycles_per_bit = FS_BIT_PERIOD_FS / CLOCK_PERIOD_FS
        lines = [f"{'case':<9} {'n':>3} {'min bits':>9} {'max bits':>9} {'max clk':>8} {'margin bits':>12}"
                 f" {'RX clk':>11} {'TX clk':>11}"]
        for case in sorted(self.bits):
            values, rx, tx = self.bits[case], self.rx_clocks[case], self.tx_clocks[case]
            worst = max(values)
            lines.append(f"{case:<9} {len(values):>3} {min(values):>9.2f} {worst:>9.2f} "
                         f"{worst * cycles_per_bit:>8.1f} {MAX_TURNAROUND_BITS - worst:>12.2f}"
                         f" {min(rx):>5.1f}-{max(rx):<5.1f} {min(tx):>5.1f}-{max(tx):<5.1f}")
        return "\n".join(lines)


class TurnaroundProbe:
    """Records the end of each host EOP and the start of the next device packet."""

    def __init__(self, dut) -> None:
        self.dut = dut
        self.host_eop_end_fs: int | None = None
        self.device_start_fs: int | None = None
        self.device_packets = 0
        self.rx_valid_fs: int | None = None
        self.tx_valid_fs: int | None = None
        cocotb.start_soon(self._watch_host_eop())
        cocotb.start_soon(self._watch_device_start())
        cocotb.start_soon(self._watch_valid(dut.rx_packet_valid, "rx_valid_fs"))
        cocotb.start_soon(self._watch_valid(dut.tx_packet_valid, "tx_valid_fs"))

    def arm(self) -> None:
        self.host_eop_end_fs = None
        self.device_start_fs = None
        self.device_packets = 0
        self.rx_valid_fs = None
        self.tx_valid_fs = None

    def rx_clocks(self) -> float:
        """Host EOP end to the parser's record of the packet being answered."""
        return (self.rx_valid_fs - self.host_eop_end_fs) / CLOCK_PERIOD_FS

    def tx_clocks(self) -> float:
        """Response packet record to the first driven state."""
        return (self.device_start_fs - self.tx_valid_fs) / CLOCK_PERIOD_FS

    async def _watch_valid(self, signal, name: str) -> None:
        while True:
            await RisingEdge(self.dut.clk)
            if int(signal.value) and self.device_start_fs is None:
                setattr(self, name, get_sim_time(unit="fs"))

    def turnaround_bits(self) -> float:
        assert self.host_eop_end_fs is not None, "no host EOP seen"
        assert self.device_start_fs is not None, "the device did not respond"
        return (self.device_start_fs - self.host_eop_end_fs) / FS_BIT_PERIOD_FS

    def _line(self) -> tuple[str, str]:
        return str(self.dut.usb_dp.value).upper(), str(self.dut.usb_dn.value).upper()

    async def _watch_host_eop(self) -> None:
        previous = self._line()
        while True:
            await First(Edge(self.dut.usb_dp), Edge(self.dut.usb_dn))
            line = self._line()
            se0_to_j = previous in {("0", "0"), ("L", "L"), ("0", "L"), ("L", "0")} and line[0] in "1H" and line[1] in "0L"
            if se0_to_j and not int(self.dut.usb_oe.value) and self.device_start_fs is None:
                self.host_eop_end_fs = get_sim_time(unit="fs")
            previous = line

    async def _watch_device_start(self) -> None:
        while True:
            await RisingEdge(self.dut.usb_oe)
            self.device_packets += 1
            if self.device_start_fs is None:
                self.device_start_fs = get_sim_time(unit="fs")


async def _setup(dut) -> tuple[UsbFsHost, TurnaroundProbe, random.Random]:
    seed = int(os.environ.get("SEED", "0"), 0) or random.SystemRandom().getrandbits(32)
    dut._log.info("SEED=%d", seed)
    dut.usb_dp_host.value = "Z"
    dut.usb_dn_host.value = "Z"
    dut.in_length.value = 0
    dut.payload_latency.value = PAYLOAD_LATENCIES[0]
    await start_clocks_and_reset(ClockDomain(dut.clk, dut.rst_n, CLOCK_PERIOD_NS))
    host = UsbFsHost(UsbFsBus(CocotbUsbFsPort(dut, dp_drive_name="usb_dp_host", dn_drive_name="usb_dn_host")))
    return host, TurnaroundProbe(dut), random.Random(seed)


async def _random_phase(rng: random.Random) -> None:
    await Timer(rng.randrange(FS_BIT_PERIOD_FS * 10, FS_BIT_PERIOD_FS * 30), unit="fs")


SETTLE_BITS = 40
"""Long enough for a second, unwanted device packet (e.g. an answer to its own DATA)."""


async def _measure(probe: TurnaroundProbe, transaction, case: str):
    probe.arm()
    result = await transaction
    assert result.success, f"{case}: {result}"
    await Timer(SETTLE_BITS * FS_BIT_PERIOD_FS, unit="fs")
    assert probe.device_packets == 1, f"{case}: the device sent {probe.device_packets} packets, expected 1"
    return probe.turnaround_bits(), result


@cocotb.test()
async def test_turnaround(dut):
    host, probe, rng = await _setup(dut)
    turnarounds = Turnarounds()

    for _ in range(TRANSACTIONS_PER_CASE):
        length = rng.choice(OUT_LENGTHS)
        await _random_phase(rng)
        bits, result = await _measure(probe, host.out(bytes(rng.randrange(256) for _ in range(length)),
                                                      pid=Pid.DATA0), f"OUT {length}")
        assert result.pid is Pid.ACK, f"OUT {length} not ACKed: {result}"
        turnarounds.add(f"OUT {length}", bits, probe)

        await _random_phase(rng)
        bits, result = await _measure(probe, host.setup(bytes(rng.randrange(256) for _ in range(8))), "SETUP")
        assert result.pid is Pid.ACK, f"SETUP not ACKed: {result}"
        turnarounds.add("SETUP", bits, probe)

        length = rng.choice(IN_LENGTHS)
        latency = rng.choice(PAYLOAD_LATENCIES)
        case = f"IN {length:>2} +{latency}"
        dut.in_length.value = length
        dut.payload_latency.value = latency
        await _random_phase(rng)
        bits, result = await _measure(probe, host.in_transfer(pid=Pid.DATA1), case)
        assert result.payload == bytes(range(length)), f"{case} payload {result.payload.hex()}"
        turnarounds.add(case, bits, probe)

    dut._log.info("turnaround (host EOP end -> device SYNC start), limit %.1f bit times:\n%s",
                  MAX_TURNAROUND_BITS, turnarounds.report())
    for case, values in turnarounds.bits.items():
        assert min(values) >= MIN_TURNAROUND_BITS, f"{case}: response after {min(values):.2f} bit times"
        assert max(values) <= MAX_TURNAROUND_BITS, f"{case}: response after {max(values):.2f} bit times"
