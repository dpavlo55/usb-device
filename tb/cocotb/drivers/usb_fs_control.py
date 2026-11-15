"""Control transfers for the cocotb USB full-speed host model.

``UsbFsControl`` runs whole control transfers on endpoint 0 of a :class:`UsbFsHost`:
setup, data and status stages with their data toggles, retrying NAKed data and
status packets as a host does. A request the device refuses ends with STALL, which
is returned as ``None``.
"""
from __future__ import annotations

import struct

from cocotb.triggers import Timer

try:  # Works both as tb.cocotb.drivers and after cocotb's sys.path insertion.
    from ..models.usb_fs_types import Pid
    from .usb_fs_host import UsbFsHost
except ImportError:  # pragma: no cover - exercised by the cocotb loader layout
    from models.usb_fs_types import Pid
    from drivers.usb_fs_host import UsbFsHost


def setup_packet(request_type: int, request: int, value: int = 0, index: int = 0, length: int = 0) -> bytes:
    return struct.pack("<BBHHH", request_type, request, value, index, length)


class ControlStall(Exception):
    """The device answered a control transfer stage with STALL."""


class UsbFsControl:
    def __init__(self, host: UsbFsHost, max_packet: int = 64, retries: int = 1000, retry_ns: int = 2000) -> None:
        self.host = host
        self.max_packet = max_packet
        self.retries = retries
        self.retry_ns = retry_ns

    async def _in(self, pid: Pid):
        for _ in range(self.retries):
            result = await self.host.in_transfer(0, pid=pid)
            if result.pid is Pid.STALL:
                raise ControlStall()
            if result.pid is not Pid.NAK:
                assert result.success, f"control IN expecting {pid.name}: {result}"
                return result.payload
            await Timer(self.retry_ns, unit="ns")
        raise AssertionError(f"control IN: NAKed {self.retries} times")

    async def _out(self, payload: bytes, pid: Pid) -> None:
        for _ in range(self.retries):
            result = await self.host.out(payload, 0, pid=pid)
            if result.pid is Pid.STALL:
                raise ControlStall()
            if result.pid is not Pid.NAK:
                assert result.pid is Pid.ACK, f"control OUT {pid.name}: {result}"
                return
            await Timer(self.retry_ns, unit="ns")
        raise AssertionError(f"control OUT: NAKed {self.retries} times")

    async def _setup(self, setup: bytes) -> None:
        result = await self.host.setup(setup)
        assert result.success, f"SETUP {setup.hex()} was not ACKed: {result}"

    async def read(self, setup: bytes, early_status: bool = False) -> bytes | None:
        """Control read; with ``early_status`` the host ends it after the first data packet."""
        length = struct.unpack_from("<H", setup, 6)[0]
        try:
            await self._setup(setup)
            data, pid = b"", Pid.DATA1
            while len(data) < length:
                packet = await self._in(pid)
                data += packet
                pid = Pid.DATA0 if pid is Pid.DATA1 else Pid.DATA1
                if len(packet) < self.max_packet or early_status:
                    break
            await self._out(b"", Pid.DATA1)
            return data
        except ControlStall:
            return None

    async def write(self, setup: bytes, data: bytes = b"") -> bool:
        """Control write (or no-data request); False if the device refused it."""
        try:
            await self._setup(setup)
            pid = Pid.DATA1
            for offset in range(0, len(data), self.max_packet):
                await self._out(data[offset:offset + self.max_packet], pid)
                pid = Pid.DATA0 if pid is Pid.DATA1 else Pid.DATA1
            status = await self._in(Pid.DATA1)
            assert status == b"", f"status stage carried data {status.hex()}"
            return True
        except ControlStall:
            return False
