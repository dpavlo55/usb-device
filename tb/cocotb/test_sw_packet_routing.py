"""Host side of the ``packet_routing`` firmware test (``sw/tests/packet_routing``).

Run with ``python tools/cocotb_runner.py --sw-test packet_routing``. The device
has address 5 with OUT entries on endpoints 1 and 3 and an IN entry on endpoint
1. DATA after an OUT token to another address, or after an IN token, must not be
written; DATA after an OUT token must land in the buffer of the entry serving
that endpoint. The firmware checks the buffers and the interrupts.
"""
import cocotb
from cocotb.triggers import with_timeout

from drivers.usb_fs_bus import CocotbUsbFsPort, UsbFsBus
from drivers.usb_fs_host import UsbFsHost
from models.usb_fs_types import DataPacket, Pid, TokenPacket
from test_usb_sw import wait_for_sw_result
from test_usb_top import start_clocks_and_release_reset

DEVICE_ADDRESS = 5
DATA_1 = bytes((0x10 + 3 * i) & 0xFF for i in range(12))
DATA_3 = bytes(0xA0 ^ i for i in range(7))
FOREIGN = bytes(range(20))
RESPONSE_TIMEOUT_BITS = 18


@cocotb.test()
async def test_sw_packet_routing(dut) -> None:
    sw_result = cocotb.start_soon(wait_for_sw_result(dut))
    # Returns once the firmware has armed the entries and raised the pull-up.
    await with_timeout(start_clocks_and_release_reset(dut), 5, "ms")
    host = UsbFsHost(UsbFsBus(CocotbUsbFsPort(dut, dp_drive_name="usb_dp_host", dn_drive_name="usb_dn_host")))

    # Not for the device: no answer, nothing written.
    for token in (TokenPacket(Pid.OUT, DEVICE_ADDRESS + 1, 1), TokenPacket(Pid.IN, DEVICE_ADDRESS, 1)):
        await host.send_packet(token)
        await host.send_packet(DataPacket(Pid.DATA0, FOREIGN))
        response = await host.bus.capture_packet(timeout_cells=64, sop_timeout_cells=RESPONSE_TIMEOUT_BITS)
        assert response is None, f"{token}: the device answered {response}"

    host.address = DEVICE_ADDRESS
    for endpoint, payload in ((3, DATA_3), (1, DATA_1)):
        result = await host.out(payload, endpoint, pid=Pid.DATA0)
        assert result.pid is Pid.ACK, f"OUT to endpoint {endpoint}: answered {result.pid}, expected ACK"

    await sw_result
