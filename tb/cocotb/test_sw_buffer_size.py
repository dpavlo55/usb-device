"""Host side of the ``buffer_size`` firmware test (``sw/tests/buffer_size``).

Run with ``python tools/cocotb_runner.py --sw-test buffer_size``. The firmware gives
endpoint 1 OUT and IN 8-byte buffers (``size`` 0), with another endpoint's buffer
right after the OUT one. An OUT data packet longer than the buffer is a packet error
the device answers with no handshake (USB 2.0 sections 8.5.2, 8.6.4); the host then
sends a packet that fits, which completes. An IN armed with ``transfer.length`` 20
sends only the 8 buffer bytes. The firmware checks the neighbour buffer and the
events. Packet ``n`` carries bytes ``(n << 4) + i``.
"""
import cocotb
from cocotb.triggers import with_timeout

from drivers.usb_fs_bus import CocotbUsbFsPort, UsbFsBus
from drivers.usb_fs_host import UsbFsHost
from models.usb_fs_types import DataPacket, Pid, TokenPacket
from test_usb_sw import PullUpToggles, wait_for_sw_result
from test_usb_top import start_clocks_and_release_reset

ENDPOINT = 1
BUFFER_BYTES = 8
RESPONSE_TIMEOUT_BITS = 18


def _packet(n: int, length: int) -> bytes:
    return bytes(((n << 4) + i) & 0xFF for i in range(length))


@cocotb.test()
async def test_sw_buffer_size(dut) -> None:
    sw_result = cocotb.start_soon(wait_for_sw_result(dut))
    await with_timeout(start_clocks_and_release_reset(dut), 5, "ms")
    host = UsbFsHost(UsbFsBus(CocotbUsbFsPort(dut, dp_drive_name="usb_dp_host", dn_drive_name="usb_dn_host")))
    phases = PullUpToggles(dut)

    await host.send_packet(TokenPacket(Pid.OUT, 0, ENDPOINT))
    await host.send_packet(DataPacket(Pid.DATA0, _packet(0xA, 2 * BUFFER_BYTES)))
    response = await host.bus.capture_packet(timeout_cells=64, sop_timeout_cells=RESPONSE_TIMEOUT_BITS)
    assert response is None, f"OUT longer than the 8-byte buffer: the device answered {response}"

    result = await host.out(_packet(0xB, BUFFER_BYTES), ENDPOINT, pid=Pid.DATA0)
    assert result.pid is Pid.ACK, f"OUT of 8 bytes after the rejected one: answered {result.pid}, expected ACK"

    await phases.next()
    result = await host.in_transfer(ENDPOINT, pid=Pid.DATA0)
    assert result.success, f"IN from the 8-byte buffer: {result}"
    assert result.payload == _packet(0x5, BUFFER_BYTES), (
        f"IN armed with length 20 from an 8-byte buffer: got {result.payload.hex()}, expected the 8 buffer bytes")

    await sw_result
