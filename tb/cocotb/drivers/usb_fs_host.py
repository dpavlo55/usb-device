"""High-level, testable USB full-speed host transaction model."""
from __future__ import annotations

try:  # Works both as tb.cocotb.drivers and after cocotb's sys.path insertion.
    from ..models.usb_fs_codec import encode_packet
    from ..models.usb_fs_timing import reset_duration_fs, timeout_fs
    from ..models.usb_fs_types import (
        DataPacket,
        HandshakePacket,
        LineState,
        Packet,
        Pid,
        SofPacket,
        TokenPacket,
        TransactionResult,
    )
    from .usb_fs_bus import UsbFsBus
except ImportError:  # pragma: no cover - exercised by the cocotb loader layout
    from models.usb_fs_codec import encode_packet
    from models.usb_fs_timing import reset_duration_fs, timeout_fs
    from models.usb_fs_types import (
        DataPacket,
        HandshakePacket,
        LineState,
        Packet,
        Pid,
        SofPacket,
        TokenPacket,
        TransactionResult,
    )
    from drivers.usb_fs_bus import UsbFsBus


INTER_PACKET_DELAY_BITS = 2
"""USB 2.0 7.1.18.1: a host transmits at least two bit times after the end of a
device packet; until then the device may still be driving its EOP."""


class UsbFsHost:
    """USB host control/data transactions over an abstract :class:`UsbFsBus`."""

    def __init__(self, bus: UsbFsBus, address: int = 0, response_timeout_bits: int = 18) -> None:
        if not 0 <= address <= 127:
            raise ValueError("USB address must be in range 0..127")
        if response_timeout_bits <= 0:
            raise ValueError("response timeout must be positive")
        self.bus = bus
        self.address = address
        self.response_timeout_bits = response_timeout_bits
        self.frame = 0
        self._toggles: dict[tuple[int, int, str], Pid] = {}
        self.last_diagnostics: tuple[str, ...] = ()

    @property
    def toggles(self) -> tuple[tuple[tuple[int, int, str], Pid], ...]:
        return tuple(sorted(self._toggles.items()))

    async def bus_reset(self, duration_fs: int | None = None) -> None:
        await self.bus.drive(LineState.SE0)
        await self.bus.port.wait_fs(reset_duration_fs(duration_fs))
        await self.bus.release()
        await self.bus.port.wait_fs(timeout_fs(2))
        self._toggles.clear()

    async def send_packet(self, packet: Packet, faults=()) -> tuple:
        cells = encode_packet(packet, faults=faults)
        await self.bus.drive_cells(cells)
        return cells

    async def send_sof(self, frame: int | None = None) -> TransactionResult:
        value = self.frame if frame is None else frame
        packet = SofPacket(value)
        await self.send_packet(packet)
        self.frame = (value + 1) & 0x7FF
        return TransactionResult(
            True,
            packet.pid,
            packet,
            diagnostics=("SOF transmitted",),
            sent_packets=(packet,),
        )

    async def setup(
        self,
        request: bytes,
        endpoint: int = 0,
        *,
        pid: Pid | None = None,
    ) -> TransactionResult:
        if len(request) != 8:
            raise ValueError("a USB setup request is exactly eight bytes")
        token = TokenPacket(pid or Pid.SETUP, self.address, endpoint)
        data = DataPacket(Pid.DATA0, request)
        sent = (token, data)
        await self.send_packet(token)
        await self.send_packet(data)
        response = await self._capture_response("SETUP")
        if response is None:
            return self._timeout("SETUP response timeout", sent)
        if response.pid is Pid.ACK:
            self._toggles[(self.address, endpoint, "in")] = Pid.DATA1
            self._toggles[(self.address, endpoint, "out")] = Pid.DATA1
            return TransactionResult(
                True,
                response.pid,
                response,
                diagnostics=("SETUP acknowledged",),
                sent_packets=sent,
            )
        return self._response_failure(response, "SETUP was not acknowledged", sent)

    async def out(
        self,
        payload: bytes,
        endpoint: int = 0,
        *,
        pid: Pid | None = None,
    ) -> TransactionResult:
        token = TokenPacket(Pid.OUT, self.address, endpoint)
        key = (self.address, endpoint, "out")
        data_pid = pid or self._toggles.get(key, Pid.DATA0)
        if data_pid not in (Pid.DATA0, Pid.DATA1):
            raise ValueError("OUT data PID override must be DATA0 or DATA1")
        data = DataPacket(data_pid, bytes(payload))
        sent = (token, data)
        await self.send_packet(token)
        await self.send_packet(data)
        response = await self._capture_response("OUT")
        if response is None:
            return self._timeout("OUT response timeout", sent)
        if response.pid is Pid.ACK:
            self._toggles[key] = Pid.DATA1 if data_pid is Pid.DATA0 else Pid.DATA0
            return TransactionResult(
                True,
                response.pid,
                response,
                diagnostics=(f"OUT {data_pid.name} acknowledged",),
                sent_packets=sent,
            )
        return self._response_failure(response, "OUT was not acknowledged", sent)

    async def in_transfer(
        self,
        endpoint: int = 0,
        *,
        pid: Pid | None = None,
    ) -> TransactionResult:
        token = TokenPacket(Pid.IN, self.address, endpoint)
        key = (self.address, endpoint, "in")
        await self.send_packet(token)
        response = await self._capture_response("IN")
        if response is None:
            return self._timeout("IN response timeout", (token,))
        if isinstance(response, DataPacket):
            if pid is not None and response.pid is not pid:
                return self._response_failure(
                    response,
                    f"unexpected IN PID {response.pid.name}",
                    (token,),
                )
            if pid is None:
                expected = self._toggles.get(key, Pid.DATA0)
                if response.pid is not expected:
                    return self._response_failure(
                        response,
                        f"unexpected toggle {response.pid.name}, expected {expected.name}",
                        (token,),
                    )
            await self.send_packet(HandshakePacket(Pid.ACK))
            self._toggles[key] = Pid.DATA1 if response.pid is Pid.DATA0 else Pid.DATA0
            return TransactionResult(
                True,
                response.pid,
                response,
                response.payload,
                ("IN data received",),
                sent_packets=(token, HandshakePacket(Pid.ACK)),
            )
        return self._response_failure(response, "IN did not return a data packet", (token,))

    async def _capture_response(self, operation: str):
        capture = await self.bus.capture_packet(
            timeout_cells=1024,
            sop_timeout_cells=self.response_timeout_bits,
        )
        if capture is None:
            self.last_diagnostics = (f"{operation}: no SOP before timeout",)
            return None
        await self.bus.port.wait_fs(timeout_fs(INTER_PACKET_DELAY_BITS))
        if not capture.ok:
            self.last_diagnostics = tuple(f"{operation}: {error.code.value}: {error.message}" for error in capture.errors)
            return None
        self.last_diagnostics = (f"{operation}: response captured",)
        return capture.packet

    def _timeout(self, message: str, sent: tuple[Packet, ...]) -> TransactionResult:
        return TransactionResult(
            False,
            error=message,
            diagnostics=self.last_diagnostics,
            timed_out=True,
            sent_packets=sent,
        )

    def _response_failure(
        self,
        packet: Packet,
        message: str,
        sent: tuple[Packet, ...],
    ) -> TransactionResult:
        return TransactionResult(
            False,
            getattr(packet, "pid", None),
            packet,
            error=message,
            diagnostics=self.last_diagnostics,
            sent_packets=sent,
        )