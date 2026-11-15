import asyncio

from tb.cocotb.drivers.usb_fs_bus import BusContentionError, FakeUsbFsPort, UsbFsBus
from tb.cocotb.models.usb_fs_types import LineState


def test_fake_port_is_deterministic_and_strict() -> None:
    async def scenario() -> None:
        port = FakeUsbFsPort()
        await port.drive("host", LineState.J)
        try:
            await port.drive("device", LineState.K)
        except BusContentionError:
            pass
        else:
            raise AssertionError("contention should be rejected")
        await port.release("host")
        assert port.contentions
        assert (await port.time_fs()) == 0

    asyncio.run(scenario())


def test_bus_capture_decodes_enqueued_packet() -> None:
    async def scenario() -> None:
        from tb.cocotb.models.usb_fs_codec import encode_packet
        from tb.cocotb.models.usb_fs_types import HandshakePacket, Pid

        port = FakeUsbFsPort()
        port.enqueue_cells(encode_packet(HandshakePacket(Pid.ACK)))
        result = await UsbFsBus(port).capture_packet()
        assert result is not None and result.ok and result.packet.pid is Pid.ACK

    asyncio.run(scenario())