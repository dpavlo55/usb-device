from __future__ import annotations

from tb.cocotb.models.usb_endpoints_model import (
    DIRECTION_IN,
    DIRECTION_OUT,
    DISABLED_ENDPOINT,
    EndpointConfig,
    TransferEvent,
    UsbEndpointsModel,
)
from tb.cocotb.models.usb_ram_adapter_model import (
    NUM_ENDPOINTS,
    InOrderScoreboard,
    PacketReport,
    ReadCommand,
    StreamBeat,
    TxScoreboard,
    UsbRamAdapterModel,
    compare_ram,
    out_packet_beats,
)


def _adapter(base: dict[int, int], allowed: set[int]) -> UsbRamAdapterModel:
    model = UsbRamAdapterModel()
    model.set_base_addresses([base.get(k, 0) for k in range(NUM_ENDPOINTS)])
    model.set_write_allowed([k in allowed for k in range(NUM_ENDPOINTS)])
    return model


def _send(model, dest: int, payload: bytes):
    results = [model.out_beat(beat) for beat in out_packet_beats(dest, payload)]
    assert all(result is None for result in results[:-1])
    return results[-1]


def test_out_packet_is_written_from_base_address_and_reported() -> None:
    model = _adapter({3: 0x101}, {3})
    assert _send(model, 3, b"\x11\x22\x33") == PacketReport(3, 3, True)
    assert model.ram[0x101:0x104] == b"\x11\x22\x33"
    assert model.rx_idle


def test_out_packet_not_allowed_is_dropped_but_reported() -> None:
    model = _adapter({1: 0x40}, set())
    assert _send(model, 1, b"\xAA" * 8) == PacketReport(1, 8, False)
    assert model.ram == bytearray(model.ram_size)


def test_write_permission_is_sampled_at_the_first_beat() -> None:
    model = _adapter({2: 0x10}, set())
    beats = out_packet_beats(2, b"\x01\x02\x03")
    model.out_beat(beats[0])
    model.set_write_allowed([True] * NUM_ENDPOINTS)
    model.out_beat(beats[1])
    assert model.out_beat(beats[2]) == PacketReport(2, 3, False)
    assert model.ram == bytearray(model.ram_size)


def test_zero_length_out_packet_writes_nothing() -> None:
    model = _adapter({0: 0}, {0})
    (beat,) = out_packet_beats(0, b"")
    assert (beat.keep, beat.last) == (0, 1)
    assert model.out_beat(beat) == PacketReport(0, 0, True)
    assert model.ram == bytearray(model.ram_size)


def test_in_packet_reads_from_base_address_with_tlast() -> None:
    model = UsbRamAdapterModel()
    model.load(0x3FE, b"\x01\x02")
    command = ReadCommand(5, 0x3FE, 2)
    assert model.in_packet(command) == (StreamBeat(0x01, 1, 5, 0), StreamBeat(0x02, 1, 5, 1))
    assert model.in_report(command) == PacketReport(5, 2, True)


def test_zero_length_in_packet_is_one_empty_beat() -> None:
    assert UsbRamAdapterModel().in_packet(ReadCommand(7, 0, 0)) == (StreamBeat(None, 0, 7, 1),)


def _endpoints(**entries: EndpointConfig) -> UsbEndpointsModel:
    model = UsbEndpointsModel()
    config = [DISABLED_ENDPOINT] * NUM_ENDPOINTS
    for name, entry in entries.items():
        config[int(name.removeprefix("ep"))] = entry
    model.configure(config)
    model.set_enable_core(True)
    return model


def test_armed_out_transfer_writes_and_completes() -> None:
    model = _endpoints(ep3=EndpointConfig(0x100, True, DIRECTION_OUT))
    model.arm(3, 0)
    _send(model, 3, b"\x05\x06")
    assert model.out_result(3, 2, True) == TransferEvent(3, 2, False, False)
    assert model.ram[0x100:0x102] == b"\x05\x06"
    assert not model.armed[3]


def test_unarmed_out_packet_is_an_overrun() -> None:
    model = _endpoints(ep3=EndpointConfig(0x100, True, DIRECTION_OUT))
    model.arm(3, 0)
    _send(model, 3, b"\x05")
    model.out_result(3, 1, True)
    _send(model, 3, b"\x07\x08")
    assert model.out_result(3, 2, False) == TransferEvent(3, 0, False, True)
    assert model.ram[0x100:0x102] == b"\x05\x00"


def test_zero_length_out_transfer_sets_zlp() -> None:
    model = _endpoints(ep1=EndpointConfig(0, True, DIRECTION_OUT))
    model.arm(1, 0)
    _send(model, 1, b"")
    assert model.out_result(1, 0, True) == TransferEvent(1, 0, True, False)


def test_inactive_endpoints_give_no_event() -> None:
    model = _endpoints(
        ep1=EndpointConfig(0, False, DIRECTION_OUT),
        ep2=EndpointConfig(0x40, True, DIRECTION_IN),
        ep3=EndpointConfig(0x80, True, DIRECTION_OUT),
    )
    for k in (1, 2, 3):
        model.arm(k, 4)
    model.set_enable_core(False)
    for k in (1, 2, 3):
        assert _send(model, k, b"\xAA") is None
    assert model.in_request(2) is None
    assert model.ram == bytearray(len(model.ram))


def test_in_transfer_needs_arm_and_uses_latched_length() -> None:
    model = _endpoints(ep4=EndpointConfig(0x20, True, DIRECTION_IN))
    model.load(0x20, b"\x01\x02\x03")
    assert model.in_request(4) is None
    model.arm(4, 2)
    beats, event = model.in_request(4)
    assert beats == (StreamBeat(0x01, 1, 4, 0), StreamBeat(0x02, 1, 4, 1))
    assert event is None, "the entry waits for the host's ACK"
    assert model.in_request(4) == (beats, None), "without the ACK the same data is sent again"
    assert model.in_result(4) == TransferEvent(4, 0, False, False)
    assert model.in_request(4) is None


def test_scoreboard_accepts_expected_packets_and_ignores_empty_beat_data() -> None:
    scoreboard = TxScoreboard()
    scoreboard.expect((StreamBeat(0x10, 1, 2, 0), StreamBeat(0x20, 1, 2, 1)))
    scoreboard.expect((StreamBeat(None, 0, 3, 1),))
    scoreboard.observe(StreamBeat(0x10, 1, 2, 0))
    scoreboard.observe(StreamBeat(0x20, 1, 2, 1))
    scoreboard.observe(StreamBeat(0xEE, 0, 3, 1))
    scoreboard.check_complete()
    assert scoreboard.errors == []
    assert scoreboard.completed_packets == 2


def test_scoreboard_reports_data_dest_and_tlast_errors() -> None:
    scoreboard = TxScoreboard()
    scoreboard.expect((StreamBeat(0x10, 1, 2, 0), StreamBeat(0x20, 1, 2, 1)))
    scoreboard.observe(StreamBeat(0x11, 1, 2, 0))
    scoreboard.observe(StreamBeat(0x20, 1, 2, 1))
    assert len(scoreboard.errors) == 1
    scoreboard.expect((StreamBeat(0x30, 1, 4, 1),))
    scoreboard.observe(StreamBeat(0x30, 1, 5, 1))
    assert len(scoreboard.errors) == 2
    scoreboard.expect((StreamBeat(0x40, 1, 4, 0), StreamBeat(0x41, 1, 4, 1)))
    scoreboard.observe(StreamBeat(0x40, 1, 4, 1))
    assert len(scoreboard.errors) == 3


def test_scoreboard_reports_unexpected_and_missing_packets() -> None:
    scoreboard = TxScoreboard()
    scoreboard.observe(StreamBeat(0, 1, 0, 1))
    scoreboard.expect((StreamBeat(0, 1, 0, 1),))
    scoreboard.check_complete()
    assert len(scoreboard.errors) == 2


def test_in_order_scoreboard() -> None:
    scoreboard = InOrderScoreboard("report")
    scoreboard.expect(PacketReport(1, 2, True))
    scoreboard.expect(PacketReport(3, 0, False))
    scoreboard.observe(PacketReport(1, 2, True))
    scoreboard.observe(PacketReport(3, 1, False))
    assert len(scoreboard.errors) == 1
    scoreboard.observe(PacketReport(4, 0, True))
    scoreboard.expect(PacketReport(4, 0, True))
    assert len(scoreboard.errors) == 1
    scoreboard.observe(PacketReport(6, 0, True))
    scoreboard.check_complete()
    assert len(scoreboard.errors) == 2


def test_compare_ram_lists_differences() -> None:
    assert compare_ram(b"\x00\x01", b"\x00\x01") == []
    assert len(compare_ram(b"\x00\x01", b"\x00\x02")) == 1
