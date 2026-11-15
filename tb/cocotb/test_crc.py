"""Black-box cocotb verification of the AXI4-Stream USB CRC block."""
from __future__ import annotations

import os
import random

import cocotb
from cocotb.triggers import (
    FallingEdge,
    ReadOnly,
    ReadWrite,
    RisingEdge,
    Timer,
)

from models.usb_fs_codec import crc5_usb, crc16_usb
from drivers.clock_reset import ClockDomain, start_clocks_and_reset


def _seed() -> int:
    value = os.getenv("SEED")
    return int(value, 0) if value is not None else random.SystemRandom().getrandbits(32)


VERBOSE = os.getenv("CRC_COCOTB_VERBOSE") == "1"


async def _initial_reset(dut) -> None:
    """Shared initial sequence: reset 1 -> 0 before the first clock edge."""
    dut.tdata.value = 0
    dut.tkeep.value = 0
    dut.tvalid.value = 0
    dut.tlast.value = 0
    await start_clocks_and_reset(ClockDomain(dut.clk, dut.rst_n, 10), hold_ns=20)


async def _reset(dut) -> None:
    # A scoreboard finishes in ReadOnly; move to a fresh timestep before
    # changing inputs for the next packet.
    await Timer(1, unit="ns")
    dut.rst_n.value = 0
    dut.tdata.value = 0
    dut.tkeep.value = 0
    dut.tvalid.value = 0
    dut.tlast.value = 0
    for _ in range(2):
        await RisingEdge(dut.clk)
    await ReadWrite()
    dut.rst_n.value = 1


async def _idle_cycle(dut) -> None:
    """Insert a non-valid cycle without resetting packet state."""
    await Timer(1, unit="ns")
    await ReadWrite()
    dut.tkeep.value = 0
    dut.tvalid.value = 0
    dut.tlast.value = 0
    await RisingEdge(dut.clk)
    await ReadWrite()


async def _scoreboard(dut, expected: int, context: str) -> None:
    """Sample the CRC result one clock after the transaction is driven."""
    await RisingEdge(dut.clk)
    await ReadOnly()
    assert int(dut.crc_valid.value) == 1, f"{context}: crc_valid was not asserted"
    observed = int(dut.crc_out.value)
    assert observed == expected, (
        f"{context}: CRC mismatch: observed 0x{observed:X}, "
        f"expected 0x{expected:X}"
    )


@cocotb.test()
async def test_crc5_usb(dut) -> None:
    """Check every 11-bit, one-cycle USB CRC5 transaction."""
    seed = _seed()
    values = list(range(1 << 11))
    random.Random(seed).shuffle(values)
    cocotb.log.info("CRC5 USB seed=0x%X", seed)
    await _initial_reset(dut)

    for case_index, value in enumerate(values):
        bits = tuple((value >> bit) & 1 for bit in range(11))
        expected = crc5_usb(bits)
        context = f"seed=0x{seed:X}, case={case_index}, data=0x{value:03X}"
        result_task = cocotb.start_soon(_scoreboard(dut, expected, context))

        dut.tdata.value = value
        dut.tkeep.value = (1 << len(dut.tkeep)) - 1
        dut.tvalid.value = 1
        dut.tlast.value = 1
        await result_task
        await _idle_cycle(dut)


@cocotb.test()
async def test_crc5_usb_zero_length(dut) -> None:
    """Check empty CRC5 streams and transitions to 11-bit tokens."""
    await _initial_reset(dut)
    packets = (
        ("empty after reset", None, 0x7FF),
        ("consecutive empty", None, 0x5A5),
        ("nonempty after empty", 0x123, 0),
        ("empty after nonempty", None, 0x35A),
        ("repeated empty", None, 0x000),
        ("nonempty after repeated empty", 0x7FF, 0),
        ("zero-valued token", 0x000, 0),
        ("final empty", None, 0x7FF),
    )

    for insert_idle in (False, True):
        await _reset(dut)
        for label, value, ignored_data in packets:
            context = f"{label}, idle_between_packets={insert_idle}"
            # None means zero bits; a token whose value is zero still has
            # eleven valid bits and is not an empty packet.
            bits = ()
            if value is not None:
                bits = tuple((value >> bit) & 1 for bit in range(11))
            expected = crc5_usb(bits)
            await FallingEdge(dut.clk)
            dut.tdata.value = ignored_data if value is None else value
            dut.tkeep.value = 0 if value is None else (1 << len(dut.tkeep)) - 1
            dut.tvalid.value = 1
            dut.tlast.value = 1
            await _scoreboard(dut, expected, context)

            if insert_idle:
                await _idle_cycle(dut)

    await _idle_cycle(dut)


@cocotb.test()
async def test_crc16_usb_zero_length(dut) -> None:
    """Check empty USB DATA payloads and packet-state transitions."""
    await _initial_reset(dut)
    packets = (
        ("empty after reset", b"", 0xFF),
        ("consecutive empty", b"", 0xA5),
        ("nonempty after empty", b"\x12\x34\x56", 0),
        ("empty after nonempty", b"", 0x5A),
        ("repeated empty", b"", 0x00),
        ("nonempty after repeated empty", bytes(range(8)), 0),
        ("final empty", b"", 0xFF),
    )

    for insert_idle in (False, True):
        await _reset(dut)
        for label, payload, ignored_data in packets:
            context = f"{label}, idle_between_packets={insert_idle}"
            expected = crc16_usb(payload)
            # An empty packet still needs one valid TLAST beat. Its TKEEP
            # is zero, so even nonzero TDATA must not contribute to the CRC.
            beats = payload if payload else bytes([ignored_data])
            for byte_index, value in enumerate(beats):
                last = byte_index == len(beats) - 1
                await FallingEdge(dut.clk)
                dut.tdata.value = value
                dut.tkeep.value = (1 << len(dut.tkeep)) - 1 if payload else 0
                dut.tvalid.value = 1
                dut.tlast.value = int(last)

                if last:
                    await _scoreboard(dut, expected, context)
                else:
                    await RisingEdge(dut.clk)
                    await ReadOnly()
                    assert int(dut.crc_valid.value) == 0, (
                        f"{context}: crc_valid asserted before TLAST "
                        f"at byte {byte_index}"
                    )

            if insert_idle:
                await _idle_cycle(dut)

    await _idle_cycle(dut)


@cocotb.test()
async def test_crc16_usb(dut) -> None:
    """Check 128 randomized multi-byte USB CRC16 packets."""
    seed = _seed()
    rng = random.Random(seed)
    cocotb.log.info("CRC16 USB random seed=0x%X", seed)
    await _initial_reset(dut)

    for packet_index in range(128):
        payload = bytes(rng.randrange(256) for _ in range(rng.randint(1, 64)))
        captured_payload = bytearray()
        context = (
            f"seed=0x{seed:X}, packet={packet_index}, "
            f"length={len(payload)}"
        )
        if VERBOSE:
            cocotb.log.info(
                "CRC16 packet %d (%d bytes): %s",
                packet_index + 1,
                len(payload),
                " ".join(f"0x{value:02X}" for value in payload),
            )

        async def packet_scoreboard() -> None:
            for _ in payload:
                await RisingEdge(dut.clk)
            await ReadOnly()
            assert int(dut.crc_valid.value) == 1, (
                f"{context}: crc_valid was not asserted"
            )
            expected = crc16_usb(captured_payload)
            observed = int(dut.crc_out.value)
            assert observed == expected, (
                f"{context}: CRC mismatch: observed 0x{observed:X}, "
                f"expected 0x{expected:X}, "
                f"payload={bytes(captured_payload).hex()}"
            )
            if VERBOSE:
                cocotb.log.info(
                    "CRC16 packet %d received: crc_valid=%d crc_out=0x%04X",
                    packet_index + 1,
                    int(dut.crc_valid.value),
                    observed,
                )

        result_task = cocotb.start_soon(packet_scoreboard())

        for byte_index, value in enumerate(payload):
            await ReadWrite()
            dut.tdata.value = value
            dut.tkeep.value = (1 << len(dut.tkeep)) - 1
            dut.tvalid.value = 1
            dut.tlast.value = int(byte_index == len(payload) - 1)
            captured_payload.append(value)
            await RisingEdge(dut.clk)

        await result_task
        await _idle_cycle(dut)