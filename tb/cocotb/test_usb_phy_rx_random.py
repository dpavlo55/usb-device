"""Seeded random Full-Speed RX-PHY verification through the USB pins."""
from __future__ import annotations

import os
import random

import cocotb
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge

from drivers.usb_fs_bus import CocotbUsbFsPort, UsbFsBus
from drivers.usb_fs_host import UsbFsHost
from models.usb_fs_codec import encode_packet, encode_packet_bytes
from models.usb_fs_timing import FS_RESET_DETECT_FS
from models.usb_fs_types import DataPacket, Pid
from test_usb_top import start_clocks_and_release_reset


PAYLOAD_LENGTH = 64
POST_PACKET_DRAIN_CYCLES = 16


def _seed_from_environment() -> int:
    value = os.environ.get("SEED")
    if value is not None:
        return int(value, 0)
    return random.SystemRandom().getrandbits(32)


def _failure_context(
    *,
    seed: int,
    case_index: int,
    packet: DataPacket,
    expected: list[int],
    observed: list[int],
) -> str:
    return (
        f"seed={seed} case_index={case_index} pid={packet.pid.name} "
        f"payload_length={len(packet.payload)} payload={packet.payload.hex()} "
        f"expected={bytes(expected).hex()} observed={bytes(observed).hex()} "
        f"received_count={len(observed)} expected_count={len(expected)}"
    )


@cocotb.test()
async def test_usb_phy_rx_random(dut) -> None:
    """Check DUT RX decoding against one seeded, uniformly random data packet."""
    await start_clocks_and_release_reset(dut)

    # Observe accepted packet beats, not the removed byte-pulse interface.
    deserializer = dut.usb_top_inst.usb_packet_deserializer_inst
    # Keep the receive sink ready for this PHY byte-scoreboard test.
    dut.usb_top_inst.rx_packet_stream.ready.value = 1

    port = CocotbUsbFsPort(
        dut,
        dp_drive_name="usb_dp_host",
        dn_drive_name="usb_dn_host",
    )
    bus = UsbFsBus(port)
    host = UsbFsHost(bus)
    await host.bus_reset(FS_RESET_DETECT_FS)

    seed = _seed_from_environment()
    case_index = 0
    rng = random.Random(seed)
    payload = bytes(rng.randrange(0x100) for _ in range(PAYLOAD_LENGTH))
    packet = DataPacket(Pid.DATA1, payload)
    expected = list(encode_packet_bytes(packet))
    failure_context = dict(
        seed=seed,
        case_index=case_index,
        packet=packet,
        expected=expected,
        observed=[],
    )
    cocotb.log.info("USB RX random seed=%d (0x%X)", seed, seed)

    # Six USB-clock edges per driven cell covers the four-edge bit timing and
    # leaves margin for synchronizers, worst-case stuffing, and EOP.
    timeout_cycles = len(encode_packet(packet)) * 6 + 256
    packet_sent = False
    observed: list[int] = []

    async def monitor() -> None:
        drain_cycles_remaining: int | None = None
        eop_count = 0
        for _ in range(timeout_cycles):
            await FallingEdge(dut.clk_usb)
            await ReadOnly()
            data, keep, dest, last, valid, ready = (
                int(getattr(deserializer, f"packet_t{field}").value)
                for field in ("data", "keep", "dest", "last", "valid", "ready")
            )
            await RisingEdge(dut.clk_usb)
            await ReadOnly()
            if valid and ready:
                assert dest == 0, f"Unexpected packet TDEST={dest}"
                assert keep == int(not last), "Unexpected packet TKEEP/TLAST combination"
                if last:
                    eop_count += 1
                    assert data == 0 and eop_count == 1, "Invalid or duplicate EOP marker"
                    assert len(observed) == len(expected), "EOP arrived before all packet bytes"
            if valid and ready and keep:
                actual = data
                index = len(observed)
                observed.append(actual)
                if index >= len(expected):
                    failure_context["observed"] = observed
                    raise AssertionError(
                        "unexpected extra RX byte: "
                        f"byte_index={index} actual=0x{actual:02X} "
                        f"{_failure_context(**failure_context)}"
                    )
                if actual != expected[index]:
                    failure_context["observed"] = observed
                    raise AssertionError(
                        "RX byte mismatch: "
                        f"byte_index={index} expected=0x{expected[index]:02X} "
                        f"actual=0x{actual:02X} "
                        f"{_failure_context(**failure_context)}"
                    )
            if packet_sent and len(observed) == len(expected) and eop_count == 1:
                if drain_cycles_remaining is None:
                    drain_cycles_remaining = POST_PACKET_DRAIN_CYCLES
                elif drain_cycles_remaining == 0:
                    return
                else:
                    drain_cycles_remaining -= 1

        failure_context["observed"] = observed
        raise AssertionError(
            "RX monitor timed out: " + _failure_context(**failure_context)
        )

    monitor_task = cocotb.start_soon(monitor())
    try:
        await host.send_packet(packet)
        packet_sent = True
        await monitor_task
    finally:
        if not monitor_task.done():
            monitor_task.cancel()

    failure_context["observed"] = observed
    assert len(observed) == len(expected), _failure_context(**failure_context)
    assert observed == expected, _failure_context(**failure_context)
