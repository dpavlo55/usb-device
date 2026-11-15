"""Randomized generator verification with scoreboard failures deferred to the end."""
from __future__ import annotations

import os
import random
from fractions import Fraction

import cocotb
from cocotb.triggers import ReadOnly, ReadWrite, RisingEdge

from models.usb_fs_codec import encode_packet_bytes
from models.usb_fs_types import DataPacket, HandshakePacket, Packet, Pid
from models.usb_tx_scoreboard import ScoreboardError, UsbTxScoreboard
from drivers.clock_reset import ClockDomain, start_clocks_and_reset


POST_PACKET_DRAIN_CYCLES = 16
BACKPRESSURE_PATTERN = (0, 0, 0, 0, 0, 1, 1, 1)
MAX_PAYLOAD_DELAY_CYCLES = 40
"""SYNC and PID last 64 clocks on the wire, so a payload this late still meets the PHY."""


def _seed_from_environment() -> int:
    value = os.environ.get("SEED")
    return int(value, 0) if value is not None else random.SystemRandom().getrandbits(32)


def _packets(seed: int) -> tuple[Packet, ...]:
    rng = random.Random(seed)
    packets: list[Packet] = [
        HandshakePacket(pid) for pid in (Pid.ACK, Pid.NAK, Pid.STALL)
    ]
    packets.extend(
        DataPacket(pid, bytes(rng.randrange(256) for _ in range(length)))
        for pid, length in (
            (Pid.DATA0, 0),
            (Pid.DATA1, 1),
            (Pid.DATA0, 64),
        )
    )
    for _ in range(rng.randint(4, 8)):
        if rng.choice((False, True)):
            packets.append(HandshakePacket(rng.choice((Pid.ACK, Pid.NAK, Pid.STALL))))
        else:
            length = rng.randrange(1024)
            packets.append(
                DataPacket(
                    rng.choice((Pid.DATA0, Pid.DATA1)),
                    bytes(rng.randrange(256) for _ in range(length)),
                )
            )
    rng.shuffle(packets)
    return tuple(packets)


async def _start_clock_and_release_reset(dut) -> None:
    for name in (
        "packet_pid", "packet_valid", "payload_length", "payload_tdata",
        "payload_tkeep", "payload_tdest", "payload_tvalid", "payload_tlast",
        "packet_tready",
    ):
        getattr(dut, name).value = 0
    await start_clocks_and_reset(
        ClockDomain(dut.clk, dut.rst_n, Fraction(20833, 1000)), hold_ns=4 * 20.833
    )


async def _next_drive_cycle(clk) -> None:
    """Update inputs after this rising edge's HDL evaluation, for the next edge."""
    await RisingEdge(clk)
    await ReadWrite()


async def _drive_packet(dut, packet: Packet, payload_delay: int = 0) -> None:
    await _next_drive_cycle(dut.clk)
    dut.packet_pid.value = int(packet.pid)
    dut.packet_valid.value = 1
    dut.payload_length.value = len(packet.payload) if isinstance(packet, DataPacket) else 0
    await _next_drive_cycle(dut.clk)
    dut.packet_valid.value = 0
    if isinstance(packet, DataPacket):
        for _ in range(1 + payload_delay):
            await _next_drive_cycle(dut.clk)
        payload_length = len(packet.payload)
        for index in range(max(1, payload_length)):
            empty_payload = payload_length == 0
            dut.payload_tdata.value = packet.payload[index] if not empty_payload else 0
            dut.payload_tkeep.value = int(not empty_payload)
            dut.payload_tdest.value = 0
            dut.payload_tvalid.value = 1
            dut.payload_tlast.value = int(
                empty_payload or index == payload_length - 1
            )
            while True:
                await RisingEdge(dut.clk)
                # Sample TREADY at the transfer edge, before HDL updates it.
                ready = dut.payload_tready.value
                accepted = getattr(ready, "is_resolvable", True) and int(ready) == 1
                await ReadWrite()
                if accepted:
                    break
    dut.payload_tvalid.value = 0
    dut.payload_tlast.value = 0
    dut.payload_tkeep.value = 0
    await _next_drive_cycle(dut.clk)


def _axis_value(value: object, name: str) -> int:
    if not getattr(value, "is_resolvable", True):
        raise ScoreboardError(f"unresolved {name}")
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ScoreboardError(f"unresolved {name}") from None


async def _monitor_packet(
    dut, scoreboard: UsbTxScoreboard, timeout_cycles: int, errors: list[str]
) -> None:
    packet_failed = False
    stalled_beat = None
    # Keep the full observation window even if checking fails or completes early.
    for cycle in range(timeout_cycles + POST_PACKET_DRAIN_CYCLES):
        await RisingEdge(dut.clk)
        # Capture the beat consumed at this edge, not the next registered beat.
        sample = {
            name: getattr(dut, f"packet_t{name}").value
            for name in ("valid", "ready", "data", "keep", "dest", "last")
        }
        await ReadWrite()
        dut.packet_tready.value = BACKPRESSURE_PATTERN[cycle % len(BACKPRESSURE_PATTERN)]
        await ReadOnly()
        if packet_failed:
            continue  # This stream is out of sync; resume checking with the next packet.
        try:
            valid = _axis_value(sample["valid"], "packet_tvalid")
            ready = _axis_value(sample["ready"], "packet_tready")
            if valid not in (0, 1) or ready not in (0, 1):
                raise ScoreboardError("invalid packet TVALID/TREADY")
            beat = (
                tuple(_axis_value(sample[name], f"packet_t{name}")
                      for name in ("data", "keep", "dest", "last"))
                if valid else None
            )
            if stalled_beat is not None and beat != stalled_beat:
                raise ScoreboardError("packet output changed while stalled")
            if beat is not None:
                if beat[1] != 1:
                    raise ScoreboardError("packet_tkeep must mark the byte valid")
                if ready:
                    scoreboard.observe(1, beat[0], beat[3])
            stalled_beat = beat if valid and not ready else None
        except ScoreboardError as exc:
            message = str(exc)
            if scoreboard.context and scoreboard.context not in message:
                message += f" ({scoreboard.context})"
            errors.append(message)
            cocotb.log.error("%s", message)
            packet_failed = True
    if not packet_failed:
        try:
            scoreboard.assert_complete()
        except ScoreboardError as exc:
            errors.append(str(exc))
            cocotb.log.error("%s", exc)


@cocotb.test()
async def test_usb_packet_generator(dut) -> None:
    """Check every packet, log failures, and assert only after stimulus completes."""
    await _start_clock_and_release_reset(dut)
    seed = _seed_from_environment()
    packets = _packets(seed)
    errors: list[str] = []
    # packet_t* includes one SYNC byte before each packet's protocol bytes.
    expected_count = sum(1 + len(encode_packet_bytes(packet)) for packet in packets)
    cocotb.log.info(
        "USB TX packet-generator seed=%d (0x%X) packet_count=%d expected_bytes=%d",
        seed, seed, len(packets), expected_count,
    )
    delays = random.Random(f"{seed}:payload_delay")
    for index, packet in enumerate(packets):
        payload_delay = delays.choice((0, delays.randrange(1, MAX_PAYLOAD_DELAY_CYCLES + 1)))
        cocotb.log.info(
            "USB TX request packet_index=%d pid=%s length=%d payload_delay=%d",
            index, packet.pid.name,
            len(packet.payload) if isinstance(packet, DataPacket) else 0, payload_delay,
        )
        context = f"seed={seed} packet_index={index} packet={packet!r} payload_delay={payload_delay}"
        scoreboard = UsbTxScoreboard((packet,), context=context)
        timeout_cycles = (1 + len(encode_packet_bytes(packet))) * 4 + 64 + payload_delay
        monitor = cocotb.start_soon(_monitor_packet(dut, scoreboard, timeout_cycles, errors))
        driver = cocotb.start_soon(_drive_packet(dut, packet, payload_delay))
        try:
            await monitor
            if driver.done():
                await driver
            else:
                message = f"payload AXI4-Stream transfer timed out ({context})"
                errors.append(message)
                cocotb.log.error("%s", message)
                driver.cancel()
                await _next_drive_cycle(dut.clk)
                dut.payload_tvalid.value = 0
                dut.payload_tlast.value = 0
                dut.payload_tkeep.value = 0
        except Exception as exc:
            raise AssertionError(f"USB TX packet-generator failure {context}: {exc}") from exc
        finally:
            if not monitor.done():
                monitor.cancel()
            if not driver.done():
                driver.cancel()
    assert not errors, (
        f"USB TX seed={seed}: {len(errors)} error(s) after all "
        f"{len(packets)} requests completed:\n" + "\n".join(errors)
    )

async def _consume_until_last(dut, held_cycles: int) -> tuple[list[int], int]:
    """Take every output beat (TREADY high); return the bytes and how many came out
    in the first ``held_cycles`` cycles."""
    dut.packet_tready.value = 1
    taken: list[int] = []
    early = 0
    for cycle in range(held_cycles + 4096):
        await RisingEdge(dut.clk)
        if int(dut.packet_tvalid.value) and int(dut.packet_tready.value):
            taken.append(int(dut.packet_tdata.value))
            if cycle < held_cycles:
                early += 1
            if int(dut.packet_tlast.value):
                return taken, early
    raise AssertionError(f"no TLAST after {len(taken)} bytes")


@cocotb.test()
async def test_sync_and_pid_before_payload(dut) -> None:
    """A DATA packet starts on packet.valid: SYNC and PID leave while the payload is held
    back, so a RAM read after the IN token does not add to the turnaround."""
    await _start_clock_and_release_reset(dut)
    held = MAX_PAYLOAD_DELAY_CYCLES
    for packet in (DataPacket(Pid.DATA1, bytes((0xA5, 0x01, 0xFF, 0x00))), DataPacket(Pid.DATA0, b"")):
        expected = [0x80, *encode_packet_bytes(packet)]
        consumer = cocotb.start_soon(_consume_until_last(dut, held))
        await _drive_packet(dut, packet, payload_delay=held)
        taken, early = await consumer
        assert taken == expected, f"{packet!r}: got {bytes(taken).hex()} expected {bytes(expected).hex()}"
        assert early >= 2, f"{packet!r}: only {early} byte(s) left before the payload arrived"
        await _next_drive_cycle(dut.clk)
