import asyncio

from tb.cocotb.drivers.usb_fs_bus import FakeUsbFsPort, UsbFsBus
from tb.cocotb.drivers.usb_fs_host import UsbFsHost
from tb.cocotb.models.usb_fs_timing import FS_RESET_DEFAULT_FS, timeout_fs
from tb.cocotb.models.usb_fs_types import DataPacket, HandshakePacket, Pid


def test_host_out_tracks_data_toggle_and_ack() -> None:
    async def scenario() -> None:
        port = FakeUsbFsPort()
        port.enqueue_packet(HandshakePacket(Pid.ACK))
        host = UsbFsHost(UsbFsBus(port), address=3, response_timeout_bits=64)
        first = await host.out(b"a", endpoint=1)
        assert first.success
        port.enqueue_packet(HandshakePacket(Pid.ACK))
        second = await host.out(b"b", endpoint=1)
        assert second.success
        assert host._toggles[(3, 1, "out")] is Pid.DATA0

    asyncio.run(scenario())


def test_host_reset_uses_full_speed_default_duration() -> None:
    async def scenario() -> None:
        port = FakeUsbFsPort()
        host = UsbFsHost(UsbFsBus(port))
        await host.bus_reset()
        assert await port.time_fs() == FS_RESET_DEFAULT_FS + timeout_fs(2)

    asyncio.run(scenario())


def test_host_setup_starts_next_data_stage_with_data1() -> None:
    async def scenario() -> None:
        port = FakeUsbFsPort()
        port.enqueue_packet(HandshakePacket(Pid.ACK))
        host = UsbFsHost(UsbFsBus(port), address=0)
        result = await host.setup(b"\x00\x05\x01\x00\x00\x00\x00\x00")
        assert result.success
        assert host._toggles[(0, 0, "in")] is Pid.DATA1
        assert host._toggles[(0, 0, "out")] is Pid.DATA1

    asyncio.run(scenario())


def test_host_in_captures_data_and_timeout_is_diagnostic() -> None:
    async def scenario() -> None:
        port = FakeUsbFsPort()
        host = UsbFsHost(UsbFsBus(port), response_timeout_bits=4)
        port.enqueue_packet(DataPacket(Pid.DATA0, b"reply"))
        result = await host.in_transfer()
        assert result.success and result.payload == b"reply"
        assert tuple(packet.pid for packet in result.sent_packets) == (Pid.IN, Pid.ACK)
        timeout = await host.in_transfer()
        assert timeout.timed_out and "timeout" in timeout.error

    asyncio.run(scenario())