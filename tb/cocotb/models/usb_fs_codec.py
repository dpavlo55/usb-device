"""Pure USB 2.0 full-speed packet codec.

All bit arrays in this module are tuples of integers, least-significant bit
first.  The codec has no simulator or wall-clock dependency.
"""
from __future__ import annotations

from typing import Iterable, Sequence

from .usb_fs_timing import FS_SYNC_BITS
from .usb_fs_types import (
    DataPacket,
    DecodeError,
    DecodeErrorCode,
    DecodeResult,
    Fault,
    FaultKind,
    HandshakePacket,
    LineState,
    Packet,
    Pid,
    SofPacket,
    SpecialPacket,
    TokenPacket,
    WireCell,
)


def byte_to_bits(value: int) -> tuple[int, ...]:
    if not 0 <= value <= 0xFF:
        raise ValueError("byte must be in range 0..255")
    return tuple((value >> i) & 1 for i in range(8))


def bits_to_int(bits: Iterable[int]) -> int:
    result = 0
    for i, bit in enumerate(bits):
        if bit not in (0, 1):
            raise ValueError("bits must contain only zero or one")
        result |= bit << i
    return result


def bits_to_byte(bits: Iterable[int]) -> int:
    values = tuple(bits)
    if len(values) != 8:
        raise ValueError("exactly eight bits are required")
    return bits_to_int(values)


def crc5_usb(bits: Iterable[int]) -> int:
    crc = 0x1F
    for bit in bits:
        if bit not in (0, 1):
            raise ValueError("bits must contain only zero or one")
        feedback = (crc & 1) ^ bit
        crc >>= 1
        if feedback:
            crc ^= 0x14
    return crc ^ 0x1F


def crc5_token(address: int, endpoint: int) -> int:
    if not 0 <= address <= 127 or not 0 <= endpoint <= 15:
        raise ValueError("token address or endpoint is out of range")
    token = address | (endpoint << 7)
    return crc5_usb((token >> index) & 1 for index in range(11))


def crc16_usb(data: bytes | bytearray | Iterable[int]) -> int:
    if isinstance(data, (bytes, bytearray)):
        values = bytes(data)
    else:
        values = bytes(data)
    crc = 0xFFFF
    for value in values:
        crc ^= value
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc ^ 0xFFFF


def bit_stuff(bits: Iterable[int]) -> tuple[int, ...]:
    output: list[int] = []
    ones = 0
    for bit in bits:
        if bit not in (0, 1):
            raise ValueError("bits must contain only zero or one")
        output.append(bit)
        if bit:
            ones += 1
            if ones == 6:
                output.append(0)
                ones = 0
        else:
            ones = 0
    return tuple(output)


def bit_unstuff(bits: Iterable[int]) -> tuple[tuple[int, ...], int]:
    output: list[int] = []
    ones = 0
    stuffed = 0
    for index, bit in enumerate(bits):
        if bit not in (0, 1):
            raise DecodeError(DecodeErrorCode.STUFF, "non-binary bit", index)
        if bit:
            ones += 1
            if ones >= 7:
                raise DecodeError(DecodeErrorCode.STUFF, "seven consecutive ones", index)
            output.append(bit)
        elif ones == 6:
            stuffed += 1
            ones = 0
        else:
            output.append(bit)
            ones = 0
    if ones == 6:
        raise DecodeError(DecodeErrorCode.STUFF, "missing stuffed zero at end of stream")
    return tuple(output), stuffed


def nrzi_encode(bits: Iterable[int], initial: LineState = LineState.J) -> tuple[LineState, ...]:
    if initial not in (LineState.J, LineState.K):
        raise ValueError("NRZI initial state must be J or K")
    state = initial
    output: list[LineState] = []
    for bit in bits:
        if bit not in (0, 1):
            raise ValueError("bits must contain only zero or one")
        if bit == 0:
            state = LineState.K if state is LineState.J else LineState.J
        output.append(state)
    return tuple(output)


def nrzi_decode(states: Iterable[LineState], initial: LineState = LineState.J) -> tuple[int, ...]:
    if initial not in (LineState.J, LineState.K):
        raise ValueError("NRZI initial state must be J or K")
    previous = initial
    bits: list[int] = []
    for state in states:
        if state not in (LineState.J, LineState.K):
            raise DecodeError(DecodeErrorCode.SE1, "non-data line state in NRZI stream")
        bits.append(1 if state is previous else 0)
        previous = state
    return tuple(bits)


def _packet_fields(packet: Packet) -> tuple[Pid, tuple[int, ...], str]:
    if isinstance(packet, TokenPacket):
        token = packet.address | (packet.endpoint << 7)
        field = tuple((token >> i) & 1 for i in range(11))
        return packet.pid, field + tuple((crc5_usb(field) >> i) & 1 for i in range(5)), "token"
    if isinstance(packet, SofPacket):
        field = tuple((packet.frame >> i) & 1 for i in range(11))
        return packet.pid, field + tuple((crc5_usb(field) >> i) & 1 for i in range(5)), "sof"
    if isinstance(packet, DataPacket):
        data_bits = tuple(bit for value in packet.payload for bit in byte_to_bits(value))
        crc = crc16_usb(packet.payload)
        return packet.pid, data_bits + tuple((crc >> i) & 1 for i in range(16)), "data"
    if isinstance(packet, HandshakePacket):
        return packet.pid, (), "handshake"
    if isinstance(packet, SpecialPacket):
        return packet.pid, (), "special"
    raise TypeError(f"unsupported packet type {type(packet)!r}")


def encode_packet_bytes(packet: Packet) -> tuple[int, ...]:
    """Return the protocol bytes before SYNC, NRZI, and bit stuffing."""
    pid, fields, _ = _packet_fields(packet)
    values = [pid.byte]
    values.extend(bits_to_byte(fields[index : index + 8]) for index in range(0, len(fields), 8))
    return tuple(values)


def pid_byte(pid: Pid) -> int:
    return Pid(pid).byte


def _faults(faults: Iterable[Fault] | None, kind: FaultKind) -> list[Fault]:
    return [fault for fault in (faults or ()) if fault.kind is kind]


def encode_packet(
    packet: Packet,
    faults: Iterable[Fault] | None = None,
    *,
    pre_faults: Iterable[Fault] | None = None,
    post_faults: Iterable[Fault] | None = None,
    include_eop: bool = True,
) -> tuple[WireCell, ...]:
    """Encode a packet into one cell per wire bit.

    ``faults`` is a convenience for faults at either logical or wire level;
    explicit ``pre_faults`` and ``post_faults`` make the injection stage clear.
    """
    all_faults = tuple(faults or ())
    pre = tuple(pre_faults or ()) + tuple(f for f in all_faults if f.kind not in (FaultKind.SE1, FaultKind.EOP, FaultKind.TRUNCATE))
    post = tuple(post_faults or ()) + tuple(f for f in all_faults if f.kind in (FaultKind.SE1, FaultKind.EOP, FaultKind.TRUNCATE))
    pid, fields, field_kind = _packet_fields(packet)
    pid_byte = pid.byte
    if _faults(pre, FaultKind.PID_COMPLEMENT):
        pid_byte ^= 0xF0
    logical = list(byte_to_bits(pid_byte)) + list(fields)
    if field_kind in ("token", "sof") and _faults(pre, FaultKind.CRC5):
        logical[8 + 11 + _faults(pre, FaultKind.CRC5)[0].index % 5] ^= 1
    if field_kind == "data" and _faults(pre, FaultKind.CRC16):
        logical[-16 + _faults(pre, FaultKind.CRC16)[0].index % 16] ^= 1
    for fault in _faults(pre, FaultKind.DATA):
        index = 8 + fault.index
        if index < len(logical):
            logical[index] ^= 1
    if _faults(pre, FaultKind.STUFF):
        stuffed = list(bit_stuff(logical))
        fault = _faults(pre, FaultKind.STUFF)[0]
        markers = [
            index for index, bit in enumerate(stuffed)
            if bit == 0 and index >= 6 and all(previous == 1 for previous in stuffed[index - 6 : index])
        ]
        if markers:
            stuffed[markers[fault.index % len(markers)]] = 1
        elif fault.index < len(stuffed):
            stuffed[fault.index] = 1
    else:
        stuffed = list(bit_stuff(logical))
    encoded = list(FS_SYNC_BITS) + stuffed
    states = list(nrzi_encode(encoded))
    cells = [WireCell(state) for state in states]
    if include_eop and not _faults(post, FaultKind.EOP):
        cells.extend((WireCell(LineState.SE0), WireCell(LineState.SE0), WireCell(LineState.J)))
    elif include_eop:
        eop_fault = _faults(post, FaultKind.EOP)[0]
        cells.extend(WireCell(LineState.SE0) for _ in range(max(0, eop_fault.value)))
        cells.append(WireCell(LineState.J))
    for fault in _faults(post, FaultKind.SE1):
        if fault.index < len(cells):
            cells[fault.index] = WireCell(LineState.SE1)
    truncations = _faults(post, FaultKind.TRUNCATE)
    if truncations:
        cells = cells[:truncations[0].index]
    return tuple(cells)


def _error(code: DecodeErrorCode, message: str, index: int | None = None) -> DecodeResult:
    return DecodeResult(None, (DecodeError(code, message, index),))


def decode_packet(cells: Sequence[WireCell | LineState]) -> DecodeResult:
    """Decode cells and report all validation failures as structured results."""
    if not cells:
        return _error(DecodeErrorCode.EMPTY, "empty wire stream")
    states = tuple(cell.state if isinstance(cell, WireCell) else cell for cell in cells)
    if any(state is LineState.SE1 for state in states):
        return _error(DecodeErrorCode.SE1, "SE1 is not a valid full-speed data state", states.index(LineState.SE1))
    try:
        eop_start = states.index(LineState.SE0)
    except ValueError:
        return _error(DecodeErrorCode.EOP, "missing EOP")
    eop = states[eop_start :]
    if len(eop) != 3 or eop[0:2] != (LineState.SE0, LineState.SE0) or eop[2] is not LineState.J:
        return _error(DecodeErrorCode.EOP, "EOP must be SE0, SE0, J", eop_start)
    try:
        encoded = nrzi_decode(states[:eop_start])
    except DecodeError as exc:
        return DecodeResult(None, (exc,), consumed_cells=len(cells), eop=True)
    if tuple(encoded[:8]) != FS_SYNC_BITS:
        return _error(DecodeErrorCode.SYNC, "invalid FS SYNC", 0)
    try:
        logical, stuffed = bit_unstuff(encoded[8:])
    except DecodeError as exc:
        return DecodeResult(None, (exc,), consumed_cells=len(cells), eop=True)
    if len(logical) < 8:
        return DecodeResult(None, (DecodeError(DecodeErrorCode.SHORT, "packet body is too short"),), stuffed, len(cells), True)
    pid_byte = bits_to_byte(logical[:8])
    low = pid_byte & 0x0F
    if ((pid_byte >> 4) & 0x0F) != (low ^ 0x0F):
        return DecodeResult(None, (DecodeError(DecodeErrorCode.PID_COMPLEMENT, "PID complement mismatch"),), stuffed, len(cells), True)
    try:
        pid = Pid(low)
    except ValueError:
        return DecodeResult(None, (DecodeError(DecodeErrorCode.PID, f"unknown PID nibble 0x{low:X}"),), stuffed, len(cells), True)
    body = logical[8:]
    try:
        if pid in (Pid.SETUP, Pid.OUT, Pid.IN):
            if len(body) != 16:
                raise DecodeError(DecodeErrorCode.SHORT, "token length is not 16 bits")
            field, crc_bits = body[:11], body[11:]
            if bits_to_int(crc_bits) != crc5_usb(field):
                raise DecodeError(DecodeErrorCode.CRC5, "CRC5 mismatch")
            token = bits_to_int(field)
            packet: Packet = TokenPacket(pid, token & 0x7F, (token >> 7) & 0x0F)
        elif pid is Pid.SOF:
            if len(body) != 16:
                raise DecodeError(DecodeErrorCode.SHORT, "SOF length is not 16 bits")
            field, crc_bits = body[:11], body[11:]
            if bits_to_int(crc_bits) != crc5_usb(field):
                raise DecodeError(DecodeErrorCode.CRC5, "CRC5 mismatch")
            packet = SofPacket(bits_to_int(field))
        elif pid in (Pid.DATA0, Pid.DATA1):
            if len(body) < 16:
                raise DecodeError(DecodeErrorCode.SHORT, "data packet has no CRC16")
            if (len(body) - 16) % 8:
                raise DecodeError(DecodeErrorCode.SHORT, "data payload is not byte-aligned")
            payload = bytes(bits_to_byte(body[i : i + 8]) for i in range(0, len(body) - 16, 8))
            if bits_to_int(body[-16:]) != crc16_usb(payload):
                raise DecodeError(DecodeErrorCode.CRC16, "CRC16 mismatch")
            packet = DataPacket(pid, payload)
        elif pid in (Pid.ACK, Pid.NAK, Pid.STALL):
            if body:
                raise DecodeError(DecodeErrorCode.SHORT, "handshake contains payload")
            packet = HandshakePacket(pid)
        elif pid is Pid.PRE:
            if body:
                raise DecodeError(DecodeErrorCode.SHORT, "special packet contains payload")
            packet = SpecialPacket(pid)
        elif pid is Pid.NYET:
            raise DecodeError(DecodeErrorCode.PID, "NYET is not supported in full-speed mode")
        else:
            raise DecodeError(DecodeErrorCode.PID, f"unsupported PID {pid.name}")
    except DecodeError as exc:
        return DecodeResult(None, (exc,), stuffed, len(cells), True)
    return DecodeResult(packet, (), stuffed, len(cells), True)


encode = encode_packet
decode = decode_packet
unstuff = bit_unstuff
stuff = bit_stuff