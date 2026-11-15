"""Seeded random valid Full-Speed packet RX-PHY verification."""
from __future__ import annotations

import os
import random

import cocotb
from cocotb.triggers import FallingEdge, ReadOnly, ReadWrite, RisingEdge

from drivers.usb_fs_bus import CocotbUsbFsPort, UsbFsBus
from drivers.usb_fs_host import UsbFsHost
from models.usb_fs_codec import encode_packet, encode_packet_bytes
from models.usb_fs_timing import (
    FS_RESET_DETECT_BITS,
    FS_RESET_DETECT_FS,
    timeout_fs,
)
from models.usb_fs_types import (
    DataPacket,
    HandshakePacket,
    Packet,
    Pid,
    SofPacket,
    TokenPacket,
)
from test_usb_top import start_clocks_and_release_reset


MIN_PACKET_COUNT = 64
MAX_PACKET_COUNT_EXCLUSIVE = 129
MAX_DATA_PAYLOAD_BYTES = 64
POST_PACKET_DRAIN_CYCLES = 16
RESET_INSERTION_INTERVAL = 4
MAX_RANDOM_RESET_BITS = FS_RESET_DETECT_BITS * 4


def _seed_from_environment() -> int:
    value = os.environ.get("SEED")
    if value is not None:
        return int(value, 0)
    return random.SystemRandom().getrandbits(32)


def _random_packet(rng: random.Random) -> Packet:
    kind = rng.choice(("data", "token", "sof", "handshake"))
    if kind == "data":
        pid = rng.choice((Pid.DATA0, Pid.DATA1))
        length = rng.randrange(MAX_DATA_PAYLOAD_BYTES + 1)
        payload = bytes(rng.randrange(0x100) for _ in range(length))
        return DataPacket(pid, payload)
    if kind == "token":
        pid = rng.choice((Pid.SETUP, Pid.OUT, Pid.IN))
        return TokenPacket(pid, rng.randrange(128), rng.randrange(16))
    if kind == "sof":
        return SofPacket(rng.randrange(0x800))
    return HandshakePacket(rng.choice((Pid.ACK, Pid.NAK, Pid.STALL)))


def _packet_payload(packet: Packet) -> bytes:
    return packet.payload if isinstance(packet, DataPacket) else b""


def _packet_details(packet: Packet) -> str:
    if isinstance(packet, DataPacket):
        return "data"
    if isinstance(packet, TokenPacket):
        return f"address={packet.address} endpoint={packet.endpoint}"
    if isinstance(packet, SofPacket):
        return f"frame={packet.frame}"
    return "handshake"


def _random_reset_duration_fs(rng: random.Random) -> tuple[int, int]:
    reset_bits = rng.randrange(FS_RESET_DETECT_BITS, MAX_RANDOM_RESET_BITS + 1)
    return reset_bits, timeout_fs(reset_bits)


def _failure_context(
    *,
    seed: int,
    case_index: int,
    packet_count: int,
    packet: Packet,
    expected: list[int],
    observed: list[int],
) -> str:
    payload = _packet_payload(packet)
    return (
        f"seed={seed} case_index={case_index} packet_count={packet_count} "
        f"kind={type(packet).__name__} pid={packet.pid.name} "
        f"details={_packet_details(packet)} payload_length={len(payload)} "
        f"payload={payload.hex()} expected={bytes(expected).hex()} "
        f"observed={bytes(observed).hex()} received_count={len(observed)} "
        f"expected_count={len(expected)}"
    )


@cocotb.test()
async def test_usb_phy_rx_packets_random(dut) -> None:
    """Check RX decoding for seeded valid packets with varied kinds and lengths."""
    await start_clocks_and_release_reset(dut)

    deserializer = dut.usb_top_inst.usb_packet_deserializer_inst
    # Keep the receive sink ready for this PHY byte-scoreboard test.
    dut.usb_top_inst.rx_packet_stream.ready.value = 1

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
    packet_count = rng.randrange(MIN_PACKET_COUNT, MAX_PACKET_COUNT_EXCLUSIVE)
    reset_count = 0
    cocotb.log.info(
        "USB RX packet-random seed=%d (0x%X) packet_count=%d",
        seed,
        seed,
        packet_count,
    )

    for case_index in range(packet_count):
        packet = _random_packet(rng)
        expected = list(encode_packet_bytes(packet))
        failure_context = dict(
            seed=seed,
            case_index=case_index,
            packet_count=packet_count,
            packet=packet,
            expected=expected,
            observed=[],
        )
        cocotb.log.info(
            "USB RX packet-random case=%d/%d kind=%s pid=%s payload_length=%d",
            case_index + 1,
            packet_count,
            type(packet).__name__,
            packet.pid.name,
            len(_packet_payload(packet)),
        )

        timeout_cycles = len(encode_packet(packet)) * 6 + 256
        packet_sent = False
        observed: list[int] = []

        async def monitor() -> None:
            drain_cycles_remaining: int | None = None
            eop_count = 0
            for _ in range(timeout_cycles):
                await FallingEdge(dut.clk_usb)
                await ReadOnly()
                data, keep, dest, last, valid, ready = (
                    int(getattr(deserializer, f"packet_t{field}").value)
                    for field in ("data", "keep", "dest", "last", "valid", "ready")
                )
                await RisingEdge(dut.clk_usb)
                await ReadOnly()
                if valid and ready:
                    assert dest == 0, f"Unexpected packet TDEST={dest}"
                    assert keep == int(not last), "Unexpected packet TKEEP/TLAST combination"
                    if last:
                        eop_count += 1
                        assert data == 0 and eop_count == 1, "Invalid or duplicate EOP marker"
                        assert len(observed) == len(expected), "EOP arrived before all packet bytes"
                if valid and ready and keep:
                    actual = data
                    index = len(observed)
                    observed.append(actual)
                    if index >= len(expected):
                        failure_context["observed"] = observed
                        raise AssertionError(
                            "unexpected extra RX byte: "
                            f"byte_index={index} actual=0x{actual:02X} "
                            f"{_failure_context(**failure_context)}"
                        )
                    if actual != expected[index]:
                        failure_context["observed"] = observed
                        raise AssertionError(
                            "RX byte mismatch: "
                            f"byte_index={index} expected=0x{expected[index]:02X} "
                            f"actual=0x{actual:02X} "
                            f"{_failure_context(**failure_context)}"
                        )
                if packet_sent and len(observed) == len(expected) and eop_count == 1:
                    if drain_cycles_remaining is None:
                        drain_cycles_remaining = POST_PACKET_DRAIN_CYCLES
                    elif drain_cycles_remaining == 0:
                        return
                    else:
                        drain_cycles_remaining -= 1

            failure_context["observed"] = observed
            raise AssertionError(
                "RX monitor timed out: " + _failure_context(**failure_context)
            )

        monitor_task = cocotb.start_soon(monitor())
        try:
            await host.send_packet(packet)
            packet_sent = True
            await monitor_task
            await RisingEdge(dut.clk_usb)
            await ReadWrite()
        finally:
            if not monitor_task.done():
                monitor_task.cancel()

        failure_context["observed"] = observed
        assert len(observed) == len(expected), _failure_context(**failure_context)
        assert observed == expected, _failure_context(**failure_context)

        if case_index + 1 < packet_count and rng.randrange(RESET_INSERTION_INTERVAL) == 0:
            reset_bits, reset_duration_fs = _random_reset_duration_fs(rng)
            reset_count += 1
            cocotb.log.info(
                "USB RX packet-random reset after case=%d/%d bits=%d duration_fs=%d",
                case_index + 1,
                packet_count,
                reset_bits,
                reset_duration_fs,
            )
            await host.bus_reset(reset_duration_fs)

    cocotb.log.info(
        "USB RX packet-random completed packet_count=%d reset_count=%d",
        packet_count,
        reset_count,
    )
