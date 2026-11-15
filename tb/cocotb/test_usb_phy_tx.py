"""Black-box serial AXI stream to USB full-speed transmit verification."""
from __future__ import annotations

import os
import random
from collections import deque
from fractions import Fraction

import cocotb
from cocotb.triggers import ReadOnly, ReadWrite, RisingEdge

from models.usb_fs_codec import byte_to_bits, encode_packet_bytes
from models.usb_fs_types import DataPacket, HandshakePacket, Packet, Pid
from models.usb_phy_tx_scoreboard import CLOCKS_PER_BIT, UsbPhyTxScoreboard
from models.usb_serializer_scoreboard import logic_value
from drivers.clock_reset import ClockDomain, start_clocks_in_reset


QUIET_CYCLES = 16


def build_packets(seed: int) -> list[Packet]:
    rng = random.Random(seed)
    packets: list[Packet] = [HandshakePacket(pid) for pid in (Pid.ACK, Pid.NAK, Pid.STALL)]
    for pid in (Pid.DATA0, Pid.DATA1):
        packets.extend(DataPacket(pid, payload) for payload in (
            b"", b"\x00", b"\xff", bytes(64), b"\xff" * 64,
            b"\xaa\x55" * 32, b"\xf0\xff\x0f\xfc" * 16,
            bytes(range(256)), rng.randbytes(1023),
            # CRC16(0xFA) = 0xFCC0: six final ones require stuffing before EOP.
            b"\xfa",
        ))
    packets.extend(
        DataPacket(Pid.DATA1 if index % 2 else Pid.DATA0, rng.randbytes(rng.randrange(129)))
        for index in range(24)
    )
    rng.shuffle(packets)
    return packets


class PhyTxBench:
    def __init__(self, dut, seed: int) -> None:
        self.dut = dut
        self.scoreboard = UsbPhyTxScoreboard(f"seed={seed}")
        self.cycle = 0
        self.stalls = 0
        self.input_plan: deque[tuple[int, int, int, int]] = deque()
        self._driven: tuple[int, int, int, int] | None = None
        self._reset = True

    async def tick(self, *, reset: bool = False) -> bool:
        """Sample the previous beat, then drive the next after the rising edge."""
        dut = self.dut
        await RisingEdge(dut.clk)
        # READY and source fields here are the values at the transfer edge,
        # before synchronous RTL can change READY for the following cycle.
        accepted = False
        if not self._reset and self._driven is not None:
            ready = logic_value(dut.serial_tready.value, "serial_tready")
            assert ready in (0, 1), "serial_tready must be one bit"
            accepted = bool(ready)
            if accepted:
                self.scoreboard.accept(*self._driven)
                assert self.input_plan.popleft() == self._driven
            else:
                self.stalls += 1
        await ReadWrite()
        # These values belong to the NEXT rising-edge transfer. Pop only an
        # accepted beat so a stalled beat stays valid and unchanged.
        self._reset = reset
        self._driven = self.input_plan[0] if self.input_plan and not reset else None
        dut.rst_n.value = int(not reset)
        dut.serial_tvalid.value = int(self._driven is not None)
        for field, value in zip(("data", "keep", "dest", "last"), self._driven or (0, 1, 0, 0)):
            getattr(dut, f"serial_t{field}").value = value
        await ReadOnly()
        oe = logic_value(dut.usb_oe.value, "usb_oe")
        if reset:
            assert oe == 0, "TX must release USB pins during reset"
            assert int(dut.usb_dp.value) == 0 and int(dut.usb_dn.value) == 0
        else:
            # Undriven pin values are intentionally ignored, including X/Z.
            dp = logic_value(dut.usb_dp.value, "usb_dp") if oe else None
            dn = logic_value(dut.usb_dn.value, "usb_dn") if oe else None
            self.scoreboard.observe(self.cycle, oe, dp, dn)
        self.cycle += 1
        return accepted

    def enqueue(self, packet: Packet) -> int:
        values = (0x80, *encode_packet_bytes(packet))
        bits = tuple(bit for value in values for bit in byte_to_bits(value))
        self.input_plan.extend(
            (bit, 1, 0, int(index == len(bits) - 1)) for index, bit in enumerate(bits)
        )
        return len(bits)

    async def send(self, packet: Packet, index: int, seed: int) -> None:
        assert not self.input_plan and self._driven is None
        bit_count = self.enqueue(packet)
        self.scoreboard.context = f"seed={seed} packet={index} pid={packet.pid.name} bits={bit_count}"
        bit_index = 0
        target_frames = self.scoreboard.observed_frames + 1
        # Covers wire duration with worst-case stuffing, plus bounded startup
        # latency. A stuck READY, OE, or missing final bit cannot hang the test.
        budget = CLOCKS_PER_BIT * (bit_count + bit_count // 6 + 4) + 256
        for _ in range(budget):
            if await self.tick():
                bit_index += 1
            if bit_index == bit_count and self.scoreboard.observed_frames == target_frames:
                break
        else:
            raise AssertionError(
                f"{self.scoreboard.context}: timed out, accepted={bit_index}/{bit_count} "
                f"output_frames={self.scoreboard.observed_frames}/{target_frames}"
            )
        self.scoreboard.assert_complete()
        for _ in range(QUIET_CYCLES):
            await self.tick()


async def start_bench(dut, seed: int):
    dut.serial_tvalid.value = 0
    dut.serial_tdata.value = 0
    dut.serial_tkeep.value = 1
    dut.serial_tdest.value = 0
    dut.serial_tlast.value = 0
    # The bench's reset ticks hold reset low and release it on a rising edge.
    (clock_task,) = await start_clocks_in_reset(ClockDomain(dut.clk, dut.rst_n, Fraction(20833, 1000)))
    return PhyTxBench(dut, seed), clock_task


@cocotb.test()
async def test_handshake_and_data(dut) -> None:
    """Check stuffing, NRZI, OE, and all four clock samples of every USB bit."""
    seed_text = os.environ.get("SEED")
    seed = int(seed_text, 0) if seed_text is not None else random.SystemRandom().getrandbits(32)
    packets = build_packets(seed)
    bench, clock_task = await start_bench(dut, seed)
    dut._log.info("USB TX seed=%d (0x%X), packets=%d", seed, seed, len(packets))
    try:
        for _ in range(4):
            await bench.tick(reset=True)
        for _ in range(QUIET_CYCLES):
            await bench.tick()
        for index, packet in enumerate(packets):
            await bench.send(packet, index, seed)
        bench.scoreboard.assert_complete()
        assert bench.scoreboard.observed_frames == len(packets)
        assert bench.scoreboard.stuffed_bits > 0, "corpus did not exercise stuffing"
        dut._log.info("Checked %d USB cells, %d stuffed bits, %d input stalls",
                      bench.scoreboard.observed_cells, bench.scoreboard.stuffed_bits, bench.stalls)
    finally:
        clock_task.cancel()


@cocotb.test()
async def test_queued_packets(dut) -> None:
    """Keep the next packet available during EOP without losing any bits."""
    packets = [
        HandshakePacket(Pid.ACK), DataPacket(Pid.DATA0, b"\xfa"),
        DataPacket(Pid.DATA1, b"\xff" * 16), HandshakePacket(Pid.NAK),
    ] * 3
    bench, clock_task = await start_bench(dut, 0)
    bench.scoreboard.context = "queued packets"
    try:
        for _ in range(4):
            await bench.tick(reset=True)
        bit_count = sum(bench.enqueue(packet) for packet in packets)
        budget = CLOCKS_PER_BIT * (bit_count + bit_count // 6 + 4 * len(packets)) + 256
        for _ in range(budget):
            await bench.tick()
            if bench.scoreboard.observed_frames == len(packets):
                break
        else:
            raise AssertionError("queued packets timed out")
        assert not bench.input_plan and bench._driven is None
        bench.scoreboard.assert_complete()
        for _ in range(QUIET_CYCLES):
            await bench.tick()
        assert bench.stalls > 0
    finally:
        clock_task.cancel()


@cocotb.test()
async def test_reset_during_stream_and_eop(dut) -> None:
    """Abort data and each EOP cell, discard buffered traffic, then restart."""
    bench, clock_task = await start_bench(dut, 0)
    try:
        for _ in range(4):
            await bench.tick(reset=True)
        for phase, target in (("data", 0), ("first SE0", 2), ("second SE0", 6), ("J", 10)):
            bench.scoreboard.context = f"reset during {phase}"
            bench.enqueue(DataPacket(Pid.DATA0, b"\xff" * 8))
            bench.enqueue(HandshakePacket(Pid.NAK))
            eop_samples = 0
            for _ in range(2048):
                await bench.tick()
                if int(dut.usb_oe.value):
                    pins = (int(dut.usb_dp.value), int(dut.usb_dn.value))
                    if target == 0:
                        break
                    if pins == (0, 0) or eop_samples:
                        eop_samples += 1
                    if eop_samples == target:
                        break
            else:
                raise AssertionError(f"never reached reset phase {phase}")

            await bench.tick(reset=True)
            bench.input_plan.clear()
            bench.scoreboard = UsbPhyTxScoreboard(f"after reset during {phase}")
            for _ in range(3):
                await bench.tick(reset=True)
            for _ in range(QUIET_CYCLES):
                await bench.tick()
            await bench.send(DataPacket(Pid.DATA1, b"\xfa"), 0, 0)
    finally:
        clock_task.cancel()
