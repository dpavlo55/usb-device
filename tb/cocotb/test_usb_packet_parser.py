"""Backpressure verification of the USB packet parser payload stream."""
from __future__ import annotations

from fractions import Fraction

import cocotb
from cocotb.triggers import ReadOnly, RisingEdge, Timer, with_timeout
from cocotbext.axi import AxiStreamBus, AxiStreamFrame, AxiStreamSink, AxiStreamSource

from models.usb_fs_codec import encode_packet_bytes
from models.usb_fs_types import DataPacket, Pid
from drivers.clock_reset import ClockDomain, start_clocks_and_reset


async def _start_clock_and_release_reset(dut) -> None:
    dut.packet_tdata.value = 0
    dut.packet_tkeep.value = 0
    dut.packet_tdest.value = 0
    dut.packet_tlast.value = 0
    dut.packet_tvalid.value = 0
    dut.payload_tready.value = 0
    await start_clocks_and_reset(ClockDomain(dut.clk, dut.rst_n, Fraction(10, 1)), hold_ns=50)
    await Timer(1, unit="ns")


async def _wait_packet_report(dut) -> tuple[int, int]:
    """Return (crc_ok, payload_length) from the first packet_valid pulse."""
    while True:
        await RisingEdge(dut.clk)
        await ReadOnly()
        if int(dut.packet_valid.value):
            return int(dut.packet_crc_ok.value), int(dut.packet_payload_length.value)


@cocotb.test()
async def test_payload_output_holds_and_resumes_after_backpressure(dut) -> None:
    """Pause the sink, check a stable payload beat, then receive the full payload."""
    await _start_clock_and_release_reset(dut)

    source = AxiStreamSource(
        AxiStreamBus.from_prefix(dut, "packet"), dut.clk, dut.rst_n,
        reset_active_level=0,
    )
    sink = AxiStreamSink(
        AxiStreamBus.from_prefix(dut, "payload"), dut.clk, dut.rst_n,
        reset_active_level=0,
    )
    sink.pause = True

    packet = DataPacket(Pid.DATA1, bytes(range(32)))
    packet_bytes = encode_packet_bytes(packet)
    frame = AxiStreamFrame(
        tdata=list(packet_bytes) + [0],
        tkeep=[1] * len(packet_bytes) + [0],
        tdest=[0] * (len(packet_bytes) + 1),
    )
    receive_task = cocotb.start_soon(sink.recv(compact=False))
    send_task = cocotb.start_soon(source.send(frame))

    stalled_beat = None
    for _ in range(500):
        await RisingEdge(dut.clk)
        await ReadOnly()
        if int(dut.payload_tvalid.value):
            assert int(dut.payload_tready.value) == 0
            beat = (
                int(dut.payload_tdata.value),
                int(dut.payload_tkeep.value),
                int(dut.payload_tdest.value),
                int(dut.payload_tlast.value),
            )
            if stalled_beat is None:
                stalled_beat = beat
            else:
                assert beat == stalled_beat, "payload output changed while stalled"
            if int(dut.packet_tready.value) == 0:
                break
    else:
        raise AssertionError("parser did not backpressure packet input while payload stalled")

    assert stalled_beat is not None, "parser did not produce a payload beat"
    for _ in range(8):
        await RisingEdge(dut.clk)
        await ReadOnly()
        assert int(dut.payload_tvalid.value) == 1
        beat = (
            int(dut.payload_tdata.value),
            int(dut.payload_tkeep.value),
            int(dut.payload_tdest.value),
            int(dut.payload_tlast.value),
        )
        assert beat == stalled_beat, "payload output changed during extended stall"

    await Timer(1, unit="ps")
    # The packet record can be reported in the same cycle the sink completes
    # the frame, so watch for it from the moment the payload is released.
    report_task = cocotb.start_soon(_wait_packet_report(dut))
    sink.pause = False
    received = await with_timeout(receive_task, 50, "us")
    await with_timeout(send_task, 50, "us")

    # The final beat closes the frame with TKEEP=0; its TDATA is don't-care.
    assert len(received.tdata) == len(packet.payload) + 1
    assert bytes(received.tdata[:-1]) == bytes(packet.payload)
    assert received.tkeep == [1] * len(packet.payload) + [0]
    assert received.tdest == [0] * (len(packet.payload) + 1)

    crc_ok, payload_length = await with_timeout(report_task, 10 * 10, "ns")
    assert crc_ok == 1, "CRC16 failed during payload backpressure"
    assert payload_length == len(packet.payload)

    for _ in range(8):
        await RisingEdge(dut.clk)
        await ReadOnly()
        assert int(dut.payload_tvalid.value) == 0, "unexpected payload beat after TLAST"