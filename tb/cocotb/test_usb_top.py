"""Cocotb RTL integration test for the USB top-level DUT."""
from fractions import Fraction

import cocotb
from cocotb.triggers import RisingEdge, Timer

from drivers.clock_reset import ClockDomain, start_clocks_and_reset
from drivers.usb_fs_bus import CocotbUsbFsPort, UsbFsBus
from drivers.usb_fs_host import UsbFsHost
from models.usb_fs_codec import decode_packet
from models.usb_fs_timing import FS_RESET_DETECT_FS
from models.usb_fs_types import DataPacket, LineState, Pid


async def start_clocks_and_release_reset(dut, wait_for_pullup=True, vbus=True) -> None:
    dut.usb_vbus.value = 0
    dut.usb_dp_host.value = "Z"
    dut.usb_dn_host.value = "Z"
    await start_clocks_and_reset(
        ClockDomain(dut.clk_sys, dut.rst_sys_n, Fraction(16667, 1000)),
        ClockDomain(dut.clk_usb, dut.rst_usb_n, Fraction(20833, 1000)),
    )
    if not vbus:
        return
    await Timer(10, unit="ns")
    dut.usb_vbus.value = 1
    if wait_for_pullup:
        await RisingEdge(dut.usb_pu)


@cocotb.test()
async def test_clock_reset_and_fs_traffic(dut) -> None:
    """Drive one real FS SOF waveform through the USB top-level DUT pins."""
    await start_clocks_and_release_reset(dut)
    assert int(dut.rst_sys_n.value) == 1
    assert int(dut.rst_usb_n.value) == 1

    port = CocotbUsbFsPort(
        dut,
        dp_drive_name="usb_dp_host",
        dn_drive_name="usb_dn_host",
    )
    bus = UsbFsBus(port)
    host = UsbFsHost(bus)
    await host.bus_reset(2 * FS_RESET_DETECT_FS)
    port.driven_cells.clear()

    result = await host.send_sof(0)
    assert result.success
    decoded = decode_packet(port.driven_cells)
    assert decoded.ok
    assert decoded.packet == result.packet

    port.driven_cells.clear()
    data_packet = DataPacket(Pid.DATA1, bytes([0xFF]) * 64)
    await host.send_packet(data_packet)
    data_decoded = decode_packet(port.driven_cells)
    assert data_decoded.ok
    assert data_decoded.packet == data_packet
    assert data_decoded.stuffed_bits > 0
    assert data_decoded.eop
    assert tuple(cell.state for cell in port.driven_cells[-3:]) == (
        LineState.SE0,
        LineState.SE0,
        LineState.J,
    )

    for _ in range(5):
        await RisingEdge(dut.clk_usb)
    assert str(dut.usb_dp.value).upper() in {"1", "H"}
    assert str(dut.usb_dn.value).upper() in {"0", "L"}
