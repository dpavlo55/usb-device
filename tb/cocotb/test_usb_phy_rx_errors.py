"""Cocotb RX-PHY error injection tests."""
from __future__ import annotations


import cocotb
from cocotb.triggers import ReadOnly, ReadWrite, RisingEdge

from drivers.usb_fs_bus import CocotbUsbFsPort, UsbFsBus
from models.usb_fs_codec import decode_packet, encode_packet
from models.usb_fs_timing import FS_RESET_DETECT_FS, timeout_fs
from models.usb_fs_types import (
    DataPacket,
    Fault,
    FaultKind,
    HandshakePacket,
    LineState,
    Pid,
    WireCell,
)
from test_usb_top import start_clocks_and_release_reset


async def drive_and_observe_error(
    bus: UsbFsBus,
    dut,
    cells: tuple[WireCell, ...],
    error_signal,
    other_signal,
) -> tuple[bool, bool]:
    """Drive one packet and return whether each RX error flag was observed."""
    seen_error = False
    seen_other_error = False

    async def monitor() -> None:
        nonlocal seen_error, seen_other_error
        for _ in range(len(cells) * 6 + 128):
            await RisingEdge(dut.clk_usb)
            await ReadOnly()
            seen_error |= bool(int(error_signal.value))
            seen_other_error |= bool(int(other_signal.value))

    monitor_task = cocotb.start_soon(monitor())
    await bus.drive_cells(cells)
    await monitor_task
    await RisingEdge(dut.clk_usb)
    await ReadWrite()
    return seen_error, seen_other_error


@cocotb.test()
async def test_usb_phy_rx_reports_errors_in_separate_packets(dut) -> None:
    """Report SYNC and stuffing errors from two independent packets."""
    await start_clocks_and_release_reset(dut)

    sync_error = dut.usb_top_inst.usb_packet_deserializer_inst.sync_error
    stuff_error = dut.usb_top_inst.usb_packet_deserializer_inst.stuff_error
    port = CocotbUsbFsPort(
        dut,
        dp_drive_name="usb_dp_host",
        dn_drive_name="usb_dn_host",
    )
    bus = UsbFsBus(port)
    await bus.drive(LineState.SE0)
    await port.wait_fs(FS_RESET_DETECT_FS)
    await bus.release()
    await port.wait_fs(timeout_fs(2))

    sync_cells = list(encode_packet(HandshakePacket(Pid.ACK)))
    sync_cells[0] = WireCell(LineState.J)
    sync_packet = tuple(sync_cells)
    sync_result = decode_packet(sync_packet)
    assert sync_result.error is not None
    assert sync_result.error.code.value == "sync"

    sync_seen, stuff_seen_in_sync_packet = await drive_and_observe_error(
        bus, dut, sync_packet, sync_error, stuff_error
    )
    assert sync_seen, "corrupted SYNC packet did not assert sync_error"
    assert not stuff_seen_in_sync_packet, "first packet unexpectedly asserted stuff_error"

    data_packet = DataPacket(Pid.DATA0, b"\xff\xff")
    stuff_packet = encode_packet(data_packet, [Fault(FaultKind.STUFF)])
    stuff_result = decode_packet(stuff_packet)
    assert stuff_result.error is not None
    assert stuff_result.error.code.value == "stuff"

    stuff_seen, sync_seen_in_stuff_packet = await drive_and_observe_error(
        bus, dut, stuff_packet, stuff_error, sync_error
    )
    assert stuff_seen, "corrupted stuffing packet did not assert stuff_error"
    assert not sync_seen_in_stuff_packet, "second packet unexpectedly asserted sync_error"
