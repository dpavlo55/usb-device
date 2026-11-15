"""Cocotb verification of consecutive USB full-speed packets."""
from __future__ import annotations

import os
import random

import cocotb
from cocotb.triggers import ReadOnly, RisingEdge

from drivers.usb_fs_bus import CocotbUsbFsPort, UsbFsBus
from drivers.usb_fs_host import UsbFsHost
from models.usb_fs_codec import encode_packet
from models.usb_fs_timing import FS_RESET_DETECT_FS
from models.usb_fs_types import Pid, SofPacket, TokenPacket
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
    text = str(value).strip().strip('"').upper()
    return text.rsplit(".", 1)[-1]


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


def _expected_fields(packet) -> tuple[str, str, int, int, int, int, int]:
    if isinstance(packet, SofPacket):
        return ("TOKEN", "SOF", 1, 0, 0, packet.frame, 1)
    return ("TOKEN", packet.pid.name, 1, packet.address, packet.endpoint, 0, 1)


def _assert_packet(record, packet) -> None:
    packet_type, pid, pid_ok, address, endpoint, frame_number, crc_ok = _expected_fields(packet)
    _assert_enum(record.packet_type, packet_type, PACKET_TYPE_ORDINALS, "packet_type")
    _assert_enum(record.pid, pid, PID_ORDINALS, "pid")
    assert _bit(record.pid_ok) == pid_ok
    assert int(record.address.value) == address
    assert int(record.endpoint.value) == endpoint
    assert int(record.frame_number.value) == frame_number
    assert _bit(record.crc_ok) == crc_ok


async def _monitor_packets(dut, expected_packets, timeout_cycles: int) -> None:
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
            cocotb.log.debug("observed parser packet %d/%d", len(observed), len(expected_packets))
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
async def test_usb_token_packets(dut) -> None:
    """Verify SETUP, OUT, IN, and SOF parser records without intervening packets."""
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
    packets = (
        TokenPacket(Pid.SETUP, address=rng.randrange(128), endpoint=rng.randrange(16)),
        TokenPacket(Pid.OUT, address=rng.randrange(128), endpoint=rng.randrange(16)),
        TokenPacket(Pid.IN, address=rng.randrange(128), endpoint=rng.randrange(16)),
        SofPacket(frame=rng.randrange(0x800)),
    )
    timeout_cycles = sum(len(encode_packet(packet)) for packet in packets) * 6 + 512
    cocotb.log.info("USB token-packet seed=%d (0x%X)", seed, seed)

    monitor_task = cocotb.start_soon(_monitor_packets(dut, packets, timeout_cycles))
    try:
        # These are intentionally adjacent: no unrelated packet is sent
        # between SETUP, OUT, IN, and SOF.
        for packet in packets:
            if isinstance(packet, TokenPacket):
                cocotb.log.info(
                    "sending packet type=%s pid=%s address=%s endpoint=%s",
                    type(packet).__name__,
                    packet.pid.name,
                    packet.address,
                    packet.endpoint
                )
            else:
                cocotb.log.info(
                    "sending packet type=%s pid=%s sof=%s",
                    type(packet).__name__,
                    packet.pid.name,
                    packet.frame
                )
            await host.send_packet(packet)
        await monitor_task
    finally:
        if not monitor_task.done():
            monitor_task.cancel()
