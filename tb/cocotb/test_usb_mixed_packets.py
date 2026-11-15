"""Seeded random ordering of one packet for each supported packet variant."""
from __future__ import annotations

import os
import random

import cocotb
from cocotb.triggers import ReadOnly, RisingEdge

from drivers.usb_fs_bus import CocotbUsbFsPort, UsbFsBus
from drivers.usb_fs_host import UsbFsHost
from models.usb_fs_codec import encode_packet
from models.usb_fs_timing import FS_RESET_DETECT_FS
from models.usb_fs_types import (
    HandshakePacket,
    Packet,
    Pid,
    SofPacket,
    SpecialPacket,
    TokenPacket,
)
from test_usb_top import start_clocks_and_release_reset


POST_PACKET_DRAIN_CYCLES = 16
PACKET_TYPE_ORDINALS = {
    "TOKEN": 0,
    "DATA": 1,
    "HANDSHAKE": 2,
    "SPECIAL": 3,
    "INVALID": 4,
}
PID_ORDINALS = {
    "OUT": 0,
    "IN": 1,
    "SOF": 2,
    "SETUP": 3,
    "DATA0": 4,
    "DATA1": 5,
    "ACK": 6,
    "NAK": 7,
    "STALL": 8,
    "PRE": 9,
    "INVALID": 10,
}


def _seed_from_environment() -> int:
    value = os.environ.get("SEED")
    return int(value, 0) if value is not None else random.SystemRandom().getrandbits(32)


def _bit(signal) -> int:
    return int(signal.value)


def _enum_name(value) -> str:
    return str(value).strip().strip('"').upper().rsplit(".", 1)[-1]


def _assert_enum(signal, expected: str, ordinals: dict[str, int], field: str) -> None:
    actual = signal.value
    candidates = (actual, getattr(actual, "value", None), str(actual))
    if any(
        _enum_name(candidate) == expected
        for candidate in candidates
        if candidate is not None
    ):
        return
    for candidate in candidates:
        try:
            if int(candidate) == ordinals[expected]:
                return
        except (TypeError, ValueError):
            pass
    raise AssertionError(f"{field} mismatch: expected {expected}, observed {actual!r}")


def _assert_packet(record, packet) -> None:
    if isinstance(packet, SofPacket):
        expected = ("TOKEN", "SOF", 0, 0, packet.frame, 1)
    elif isinstance(packet, TokenPacket):
        expected = ("TOKEN", packet.pid.name, packet.address, packet.endpoint, 0, 1)
    elif isinstance(packet, HandshakePacket):
        expected = ("HANDSHAKE", packet.pid.name, 0, 0, 0, 0)
    else:
        expected = ("SPECIAL", packet.pid.name, 0, 0, 0, 0)

    packet_type, pid, address, endpoint, frame_number, crc_ok = expected
    _assert_enum(record.packet_type, packet_type, PACKET_TYPE_ORDINALS, "packet_type")
    _assert_enum(record.pid, pid, PID_ORDINALS, "pid")
    assert _bit(record.pid_ok) == 1
    assert int(record.address.value) == address
    assert int(record.endpoint.value) == endpoint
    assert int(record.frame_number.value) == frame_number
    assert _bit(record.crc_ok) == crc_ok


async def _monitor_packets(dut, expected_packets: list[Packet], timeout_cycles: int) -> None:
    record = dut.usb_top_inst.usb_packet_parser_inst.packet
    observed = []
    for _ in range(timeout_cycles):
        await RisingEdge(dut.clk_usb)
        await ReadOnly()
        if _bit(record.valid):
            if len(observed) >= len(expected_packets):
                raise AssertionError("unexpected extra parser packet")
            packet = expected_packets[len(observed)]
            _assert_packet(record, packet)
            observed.append(packet)
            if len(observed) == len(expected_packets):
                break
    else:
        raise AssertionError(
            f"timed out waiting for parser packets: observed={len(observed)} "
            f"expected={len(expected_packets)}"
        )

    for _ in range(POST_PACKET_DRAIN_CYCLES):
        await RisingEdge(dut.clk_usb)
        await ReadOnly()
        assert not _bit(record.valid), "unexpected extra parser packet after final packet"


@cocotb.test()
async def test_usb_mixed_packets(dut) -> None:
    """Verify one randomized instance of every supported parser packet kind."""
    await start_clocks_and_release_reset(dut)

    port = CocotbUsbFsPort(
        dut,
        dp_drive_name="usb_dp_host",
        dn_drive_name="usb_dn_host",
    )
    bus = UsbFsBus(port)
    host = UsbFsHost(bus)
    await host.bus_reset(FS_RESET_DETECT_FS)

    seed = _seed_from_environment()
    rng = random.Random(seed)
    packets: list[Packet] = [
        TokenPacket(Pid.SETUP, address=rng.randrange(128), endpoint=rng.randrange(16)),
        TokenPacket(Pid.OUT, address=rng.randrange(128), endpoint=rng.randrange(16)),
        TokenPacket(Pid.IN, address=rng.randrange(128), endpoint=rng.randrange(16)),
        SofPacket(rng.randrange(0x800)),
        HandshakePacket(Pid.ACK),
        HandshakePacket(Pid.NAK),
        HandshakePacket(Pid.STALL),
        SpecialPacket(Pid.PRE),
    ]
    rng.shuffle(packets)
    timeout_cycles = sum(len(encode_packet(packet)) for packet in packets) * 6 + 512
    cocotb.log.info(
        "USB mixed-packet seed=%d (0x%X) order=%s",
        seed,
        seed,
        [packet.pid.name for packet in packets],
    )

    monitor_task = cocotb.start_soon(_monitor_packets(dut, packets, timeout_cycles))
    try:
        for packet in packets:
            cocotb.log.info(
                "sending mixed packet type=%s pid=%s",
                type(packet).__name__,
                packet.pid.name,
            )
            await host.send_packet(packet)
        await monitor_task
    finally:
        if not monitor_task.done():
            monitor_task.cancel()
