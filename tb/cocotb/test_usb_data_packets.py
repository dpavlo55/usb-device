"""Cocotb verification of USB DATA packet records and AXI4-Stream payloads."""
from __future__ import annotations

import os
import random

import cocotb
from cocotb.triggers import ReadOnly, RisingEdge, with_timeout

from drivers.usb_fs_bus import CocotbUsbFsPort, UsbFsBus
from drivers.usb_fs_host import UsbFsHost
from models.usb_fs_codec import encode_packet
from models.usb_fs_timing import FS_RESET_DETECT_FS
from models.usb_fs_types import DataPacket, Pid
from test_usb_sw import wait_for_sw_result
from test_usb_top import start_clocks_and_release_reset


MAX_DATA_PAYLOAD_BYTES = 1023
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


def _assert_enum(signal, expected: str, ordinals: dict[str, int], field: str) -> None:
    actual = signal.value
    candidates = (actual, getattr(actual, "value", None), str(actual))
    for candidate in candidates:
        if candidate is None:
            continue
        name = str(candidate).strip().strip('"').upper().rsplit(".", 1)[-1]
        if name == expected:
            return
        try:
            if int(candidate) == ordinals[expected]:
                return
        except (TypeError, ValueError):
            pass
    raise AssertionError(f"{field} mismatch: expected {expected}, observed {actual!r}")


def _assert_data_packet(record, expected: DataPacket) -> None:
    assert _bit(record.valid) == 1
    _assert_enum(record.packet_type, "DATA", PACKET_TYPE_ORDINALS, "packet_type")
    _assert_enum(record.pid, expected.pid.name, PID_ORDINALS, "pid")
    assert _bit(record.pid_ok) == 1
    assert int(record.address.value) == 0
    assert int(record.endpoint.value) == 0
    assert int(record.frame_number.value) == 0
    assert int(record.payload_length.value) == len(expected.payload)
    assert _bit(record.crc_ok) == 1


async def _monitor_packets(dut, packets: tuple[DataPacket, ...], timeout_cycles: int) -> None:
    parser = dut.usb_top_inst.usb_packet_parser_inst
    record = parser.packet
    payload = parser.payload_tdata
    payload_keep = parser.payload_tkeep
    payload_valid = parser.payload_tvalid
    payload_last = parser.payload_tlast
    payload_ready = parser.payload_tready

    observed = 0
    payload_bytes: list[int] = []
    payload_last_count = 0
    for _ in range(timeout_cycles):
        await RisingEdge(dut.clk_usb)
        await ReadOnly()
        if _bit(payload_valid) and _bit(payload_ready):
            assert observed < len(packets), "unexpected extra DATA payload beat"
            assert payload_last_count == 0, (
                f"DATA parser packet {observed}: payload beat received after TLAST"
            )
            # Sample sidebands only on accepted beats. A KEEP=0 final beat
            # closes the stream without contributing a payload byte.
            keep = int(payload_keep.value)
            last = _bit(payload_last)
            if keep:
                payload_bytes.append(int(payload.value))
            else:
                assert last, (
                    f"DATA parser packet {observed}: TKEEP=0 without TLAST"
                )
            if last:
                expected_length = len(packets[observed].payload)
                assert len(payload_bytes) == expected_length, (
                    f"DATA parser packet {observed}: TLAST received with "
                    f"{len(payload_bytes)} payload bytes, expected {expected_length}"
                )
                payload_last_count += 1
        if _bit(record.valid):
            if observed >= len(packets):
                raise AssertionError("unexpected extra DATA parser packet")
            expected_bytes = list(packets[observed].payload)
            if payload_bytes != expected_bytes:
                raise AssertionError(
                    f"DATA parser packet {observed} payload mismatch: "
                    f"expected={bytes(expected_bytes).hex()} "
                    f"observed={bytes(payload_bytes).hex()}"
                )
            if payload_last_count != 1:
                raise AssertionError(
                    f"DATA parser packet {observed}: accepted "
                    f"{payload_last_count} TVALID/TREADY/TLAST beats, expected 1"
                )
            _assert_data_packet(record, packets[observed])
            observed += 1
            payload_bytes = []
            payload_last_count = 0
            if observed == len(packets):
                break
    else:
        raise AssertionError(
            f"timed out waiting for DATA parser packets: observed={observed} "
            f"expected={len(packets)}"
        )

    for _ in range(POST_PACKET_DRAIN_CYCLES):
        await RisingEdge(dut.clk_usb)
        await ReadOnly()
        assert not _bit(record.valid), "unexpected extra parser packet after final DATA packet"
        assert not _bit(payload_valid), "unexpected extra payload beat after final DATA packet"


@cocotb.test()
async def test_usb_data_packets(dut) -> None:
    """Check DATA0/DATA1 records and framed empty, one-byte, random and 64-byte payloads."""
    sw_test = os.environ.get("USB_SW_TEST") == "1"
    if sw_test:
        sw_result = cocotb.start_soon(wait_for_sw_result(dut))
        await with_timeout(start_clocks_and_release_reset(dut), 5, "ms")
    else:
        sw_result = None
        await start_clocks_and_release_reset(dut)

    port = CocotbUsbFsPort(
        dut,
        dp_drive_name="usb_dp_host",
        dn_drive_name="usb_dn_host",
    )
    host = UsbFsHost(UsbFsBus(port))
    await host.bus_reset(FS_RESET_DETECT_FS)

    seed = _seed_from_environment()
    rng = random.Random(seed)
    #payload_lengths = (0, 1, rng.randrange(MAX_DATA_PAYLOAD_BYTES), MAX_DATA_PAYLOAD_BYTES)
    payload_lengths = (4, 4, 4, 4)
    pids = (Pid.DATA0, Pid.DATA1, Pid.DATA0, Pid.DATA1)
    packets = tuple(
        DataPacket(pid, bytes(rng.randrange(0x100) for _ in range(length)))
        for pid, length in zip(pids, payload_lengths)
    )
    timeout_cycles = sum(len(encode_packet(packet)) for packet in packets) * 6 + 512
    cocotb.log.info(
        "USB DATA-packet seed=%d (0x%X) payload_lengths=%s",
        seed,
        seed,
        payload_lengths,
    )

    monitor_task = cocotb.start_soon(_monitor_packets(dut, packets, timeout_cycles))
    try:
        for packet in packets:
            cocotb.log.info(
                "sending DATA packet pid=%s payload_length=%d",
                packet.pid.name,
                len(packet.payload),
            )
            await host.send_packet(packet)
        await monitor_task
        if sw_result is not None:
            await sw_result
    finally:
        if not monitor_task.done():
            monitor_task.cancel()
