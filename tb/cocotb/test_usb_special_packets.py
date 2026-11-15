"""Cocotb verification of a USB full-speed special PRE packet."""
from __future__ import annotations


import cocotb
from cocotb.triggers import ReadOnly, RisingEdge

from drivers.usb_fs_bus import CocotbUsbFsPort, UsbFsBus
from drivers.usb_fs_host import UsbFsHost
from models.usb_fs_codec import encode_packet
from models.usb_fs_timing import FS_RESET_DETECT_FS
from models.usb_fs_types import Pid, SpecialPacket
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
    _assert_enum(record.packet_type, "SPECIAL", PACKET_TYPE_ORDINALS, "packet_type")
    _assert_enum(record.pid, packet.pid.name, PID_ORDINALS, "pid")
    assert _bit(record.pid_ok) == 1
    assert int(record.address.value) == 0
    assert int(record.endpoint.value) == 0
    assert int(record.frame_number.value) == 0
    assert _bit(record.crc_ok) == 0


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
async def test_usb_special_packets(dut) -> None:
    """Verify that one USB SPECIAL PRE packet reaches the parser record."""
    await start_clocks_and_release_reset(dut)

    port = CocotbUsbFsPort(
        dut,
        dp_drive_name="usb_dp_host",
        dn_drive_name="usb_dn_host",
    )
    bus = UsbFsBus(port)
    host = UsbFsHost(bus)
    await host.bus_reset(FS_RESET_DETECT_FS)

    packet = SpecialPacket(Pid.PRE)
    timeout_cycles = len(encode_packet(packet)) * 6 + 512

    monitor_task = cocotb.start_soon(_monitor_packets(dut, (packet,), timeout_cycles))
    try:
        cocotb.log.info("sending special packet pid=%s", packet.pid.name)
        await host.send_packet(packet)
        await monitor_task
    finally:
        if not monitor_task.done():
            monitor_task.cancel()
