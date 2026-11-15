# Verification plan

This document lists every test in the repository, what it checks and how to run it.
It also maps the features to the tests and records what is not verified.

## Approach

- **Black-box, spec-driven checks.** Tests drive and observe ports only. Expected
  behavior comes from Python reference models (`tb/cocotb/models/`) or from the USB
  2.0 specification, never from the RTL itself.
- **Seeded randomness.** Random tests draw a fresh 32-bit seed per run and log it;
  `SEED=<n>` replays a run exactly.
- **Self-checked testbenches.** Scoreboards and checkers have their own pure-Python
  tests, including negative cases that must fail, so a broken checker cannot pass
  silently.
- **No assertions in the RTL.** All contracts are checked by the testbenches.
- **Every bug fix gets a regression test.**

Simulator: NVC (VHDL-2008) with cocotb 2.1. The pyuvm environment uses pyuvm and
pyvsc.

## Levels

| Level | Where | What it proves |
|---|---|---|
| Checker self-tests | `tb/cocotb/tests/`, `tb/uvm/unit/` (pytest, no simulator) | models, codecs and scoreboards are correct and catch the errors they exist for |
| Block | standalone VHDL harnesses in `tb/vhdl/`, one runner flag each | one RTL block against its model, including corner cases and backpressure |
| Top level | `usb_top_tb`, cocotb host on the USB pins | the PHY and packet path end to end through the real pins |
| Firmware in the loop | NEORV32 + `usb_top`, firmware in `sw/tests/` paired with a cocotb host | the register interface, interrupts and the C stack against real bus traffic |
| Compliance | pyuvm testbench in `tb/uvm/` | USB-IF USBCV Chapter 9 device tests, with random device profiles and functional coverage |
| Hardware | Arty A7-100, `tools/usb_bench.py` | enumeration and data integrity on real hosts, and throughput |

## Running

```sh
python -m pytest tb/cocotb/tests tb/uvm/unit -p no:cacheprovider   # checker self-tests
python tools/crc_cocotb_runner.py                 # CRC block
python tools/elastic_fifo_runner.py               # elastic FIFO
python tools/input_output_buffer_runner.py        # AXI4-Stream buffers
python tools/cocotb_runner.py --<block>-only      # one block harness; --help lists them
python tools/cocotb_runner.py                     # top-level suites
python tools/cocotb_runner.py --sw-test <name>    # one firmware test or app
python tb/uvm/run.py                              # all USBCV Chapter 9 tests
python tb/uvm/run.py --dut reference --selfcheck  # prove the UVM checkers catch bugs
```

A full regression runs:
- the checker self-tests
- the three standalone runners
- every `--<block>-only` harness
- the default top-level run
- every firmware test and both apps with `--sw-test`
- `tb/uvm/run.py`

It takes about 20–30 minutes on a desktop PC.

## Test inventory

### Building blocks

| Test | Runner | Checks |
|---|---|---|
| `test_crc.test_crc5_usb` | `crc_cocotb_runner.py` | every 11-bit token value against the USB CRC5 |
| `test_crc.test_crc5_usb_zero_length` | 〃 | empty CRC5 streams, then 11-bit tokens |
| `test_crc.test_crc16_usb` | 〃 | 128 random multi-byte packets against the USB CRC16 |
| `test_crc.test_crc16_usb_zero_length` | 〃 | empty DATA payloads and packet-state transitions |
| `test_elastic_fifo.*` (5 tests) | `elastic_fifo_runner.py` | empty and single beat, capacity with all sidebands, pointer wrap, reset at every occupancy, bursty random ready/valid |
| `test_input_output_buffer.*` (4 tests) | `input_output_buffer_runner.py` | lossless ordered AXI4-Stream transfer under random backpressure, sustained valid, sideband corner cases, reset flush |
| `test_cdc.test_reset_state` | `--cdc-only` | CDC primitives idle after reset |
| `test_cdc.test_pulse` | 〃 | every spaced pulse crosses as exactly one pulse, at several clock ratios |
| `test_cdc.test_handshake` | 〃 | items cross once, in order, with backpressure and bounded latency |

### PHY and packet layer

| Test | Runner | Checks |
|---|---|---|
| `test_usb_phy_tx.test_handshake_and_data` | `--phy-tx-only` | bit stuffing, NRZI, output enable and all four 48 MHz samples of every bit |
| `test_usb_phy_tx.test_queued_packets` | 〃 | the next packet waits during EOP without losing bits |
| `test_usb_phy_tx.test_reset_during_stream_and_eop` | 〃 | reset in data and in each EOP cell discards traffic and restarts cleanly |
| `test_usb_packet_serializer.test_without_backpressure` | `--serializer-only` | byte-to-bit serialization of a seeded corpus |
| `test_usb_packet_serializer.test_with_backpressure` | 〃 | the same with bounded output stalls |
| `test_usb_packet_deserializer.test_axis_stream_random` | `--deserializer-only` | bytes, KEEP, DEST and the separate EOP beat for random packets |
| `test_usb_packet_deserializer.test_axis_stream_backpressure` | 〃 | a stalled one-byte packet and its EOP marker |
| `test_usb_packet_deserializer.test_eop_marker_is_retained_while_stage_is_stalled` | 〃 | the one-shot EOP event survives a stall |
| `test_usb_packet_deserializer.test_axis_stream_consecutive_packets` | 〃 | no data leaks between back-to-back packets |
| `test_usb_packet_deserializer.test_tx_active_hides_the_bus` | 〃 | the receiver ignores the bus while the device transmits |
| `test_usb_packet_deserializer.test_reset_detected` | 〃 | bus reset detected after 2.5 µs of SE0, never on EOP or shorter SE0 |
| `test_usb_packet_parser.test_payload_output_holds_and_resumes_after_backpressure` | `--parser-only` | payload beats hold under backpressure and resume complete |
| `test_usb_packet_generator.test_usb_packet_generator` | `--generator-only` | random handshake and DATA packets: SYNC, PID, payload, CRC16 |
| `test_usb_packet_generator.test_sync_and_pid_before_payload` | 〃 | SYNC and PID leave before the payload arrives (turnaround budget) |
| `test_usb_top.test_clock_reset_and_fs_traffic` | default | one real SOF waveform through the top-level pins |
| `test_usb_phy_rx_random.test_usb_phy_rx_random` | default | a random 64-byte DATA1 packet decoded through the pins |
| `test_usb_phy_rx_packets_random.test_usb_phy_rx_packets_random` | default | 500–1000 random packets of every kind and length, with bus resets between them |
| `test_usb_phy_rx_errors.test_usb_phy_rx_reports_errors_in_separate_packets` | default | SYNC and bit-stuffing errors reported per packet |
| `test_usb_token_packets`, `test_usb_data_packets`, `test_usb_handshake_packets`, `test_usb_special_packets`, `test_usb_mixed_packets` | default, `--packet-only` | parser records for SETUP/OUT/IN/SOF, DATA0/1 (empty to 64 bytes), ACK/NAK/STALL, PRE, and a random mix of all |
| `test_usb_turnaround.test_turnaround` | `--turnaround-only` | host EOP to device SYNC within USB 2.0 §7.1.18.1 limits |
| `test_usb_firmware.test_usb_firmware` | default, `--firmware-only` | NEORV32 boots project firmware and prints registers over UART0 |

### Transaction and endpoint layer

| Test | Runner | Checks |
|---|---|---|
| `test_usb_protocol.test_routing` | `--protocol-only` | tokens select the right entry by address, number and direction; bad tokens, unknown endpoints, SETUP to non-control endpoints, a disabled core and duplicate entries select none |
| `test_usb_protocol.test_setup_response` | 〃 | a valid SETUP is ACKed even when unarmed or stalled; bad CRC or PID, DATA1, wrong length, another address or a reset between token and data get no answer |
| `test_usb_protocol.test_out` | 〃 | OUT ACK, NAK with overrun, STALL; a repeated toggle is ACKed and dropped; bad CRC gets no answer and keeps the toggle |
| `test_usb_protocol.test_out_buffer_size` | 〃 | oversize OUT data gets no handshake and keeps the toggle (USB 2.0 §8.5.2, §8.6.4) |
| `test_usb_protocol.test_in` | 〃 | IN data with the right toggle; only a valid, timely ACK completes and flips it; retries resend; NAK when unarmed; STALL |
| `test_usb_protocol.test_in_busy` | 〃 | an IN token while the RAM read is still busy is NAKed without moving the toggle |
| `test_usb_protocol.test_double_buffer_out` / `_in` | 〃 | paired entries alternate, `next_buffer` follows completions |
| `test_usb_protocol.test_double_buffer_ignored` | 〃 | `double_buffer` on a control entry or on entry 15 (no partner) does not pair anything |
| `test_usb_protocol.test_double_buffer_in_split` | 〃 | a split transfer keeps its buffer until its last packet |
| `test_usb_protocol.test_random` | 〃 | random tokens, entries and pairs against the protocol model |
| `test_usb_endpoints.test_reset_state` | `--endpoints-only` | nothing armed, no events, RAM untouched after reset |
| `test_usb_endpoints.test_out_transfers` | 〃 | armed OUT entries write RAM and report byte count and ZLP |
| `test_usb_endpoints.test_overrun` | 〃 | OUT to an unarmed entry is dropped and raises `overrun` |
| `test_usb_endpoints.test_in_transfers` | 〃 | armed IN entries send `length` bytes once per arm |
| `test_usb_endpoints.test_buffer_size` | 〃 | each entry owns 2^(size+3) bytes; nothing written past it |
| `test_usb_endpoints.test_inactive_endpoints` | 〃 | disabled entries, wrong direction or disabled core: no transfer |
| `test_usb_endpoints.test_arm_before_enable` | 〃 | an arm made while disabled is used after enable |
| `test_usb_endpoints.test_foreign_packets` | 〃 | packets for other devices write nothing and raise nothing |
| `test_usb_endpoints.test_device_address_crossing` | 〃 | the address settles in `clk_usb` on the last value written |
| `test_usb_endpoints.test_setup` | 〃 | SETUP data written only after acceptance, even when unarmed |
| `test_usb_endpoints.test_control_crossing` | 〃 | stall crosses as a level, `reset_toggle` as one pulse |
| `test_usb_endpoints.test_in_resend` | 〃 | without ACK the same IN data is resent; isochronous completes without ACK |
| `test_usb_endpoints.test_in_split` | 〃 | split IN packets, resend of the current packet, completion on the last one |
| `test_usb_endpoints.test_random_operations` | 〃 | random arms, packets and reconfiguration against the model |
| `test_usb_ram_usb_adapter.*` (9 tests) | `--ram-usb-adapter-only` | OUT writes and IN reads at every endpoint's base, write permission, zero-length packets, concurrent traffic, OUT priority |
| `test_usb_rx_filter.test_size_limit_every_size` | `--rx-filter-only` | packets just inside, at and past every buffer size |
| `test_usb_rx_filter.test_drop_and_route` | 〃 | rejected packets vanish, accepted ones carry entry and SETUP flag |
| `test_usb_rx_filter.test_random` | 〃 | random routes, sizes and backpressure |
| `test_usb_setup_stage.*` (8 tests) | `--setup-stage-only` | SETUP held until accepted, rejected SETUPs write nothing, 8-byte cap, early acceptance, back-to-back SETUPs |
| `test_usb_bus_state.*` (12 tests) | `--bus-state-only` | suspend after 3 ms idle, activity keeps awake, resume on K or reset, remote wakeup timing (5 ms idle, K drive, host answer, cancel), SOF frame number |

### Firmware in the loop

Each test pairs `sw/tests/<name>/main.c`, which runs on NEORV32, with
`tb/cocotb/test_sw_<name>.py`, which acts as the host. They run with
`--sw-test <name>`.

| Test | Checks |
|---|---|
| `endpoint_ram_sw` | CPU writes and reads every word of the shared RAM |
| `endpoint_ram_hw` | firmware attaches; USB packet suites run alongside |
| `ram_size` | `status.ram_words` matches the generated header, no address aliasing, buffers at the top of RAM work; passed with `RAM_ADDR_WIDTH` set to 10, 11, 12, 13, 15 and 16 (1 to 64 KiB) |
| `ram_bench` | CPU cycles per access to registers and shared RAM |
| `bus_reset` | `device_address` cleared and `bus_event.bus_reset` set by a bus reset; write-1-to-clear |
| `usb_interrupt` | `irq` gating by `interrupt_enable`, bus-event and endpoint sources, clear drops the line |
| `vbus` | pull-up gated by VBUS in hardware, `bus_event.vbus` on plug and unplug raises `irq`/MEIP, masking, `USB_HAL_EVENT_VBUS` |
| `endpoint_transfer` | OUT transfer completes, unarmed packet is an overrun, zero-length packet |
| `out_handshake` | OUT ACK, NAK and overrun, old-toggle packet dropped, STALL, bad CRC ignored |
| `in_handshake` | IN data and ACK, NAK when unarmed, resend without ACK, STALL |
| `setup` | SETUP accepted while stalled, invalid SETUPs (bad CRC, DATA1, 7 or 12 bytes) write nothing |
| `packet_routing` | packets for other addresses and endpoints land nowhere; each entry gets only its own |
| `buffer_size` | oversize OUT gets no handshake; IN limited to the buffer |
| `double_buffer` | register-level ping-pong for OUT and IN, back-to-back ACKs, `next_buffer` |
| `in_split` | split IN transfers on single and paired entries, short and full last packets |
| `usb_ll_ram` | layer 1 register access and RAM copies at every alignment, with cycle limits |
| `usb_hal` | layer 2 transfers, events, ZLP, stall |
| `hal_double` | HAL double buffering in arrival order, split transfers with and without ZLP |
| `suspend` | suspend, remote wakeup timing on the wire, resume, through the HAL |
| `cdc_echo` (app) | full enumeration and CDC-ACM echo of random data |
| `cdc_bench` (app) | benchmark commands and data checks; `test_sw_cdc_bench_stream` requires 0 NAKs and under 53 µs per packet in both directions |

### USB-IF Chapter 9 compliance (pyuvm)

`tb/uvm/run.py` runs the USBCV device tests against the RTL, or against a behavioral
reference device with `--dut reference`.
- **Device under test:** each run builds a constrained-random device profile:
  configurations, interfaces, alternate settings, endpoints, strings, BOS and IADs.
- **Checking:** a scoreboard, a protocol checker (toggles, turnaround, addressing)
  and a Chapter 9 state model check every run.
- **Coverage:** pyvsc collects functional coverage into UCIS XML.

| TD | Test |
|---|---|
| 9.1 / 9.2 / 9.4 / 9.5 | device, configuration, interface and endpoint descriptors in every device state |
| 9.3 | interface association descriptors |
| 9.7 / 9.30 | BOS and configuration summary descriptors (STALL or skip for bcdUSB ≤ 0x0200) |
| 9.8 | string descriptors, every index and LANGID |
| 9.9 | endpoint halt set and cleared on every bulk and interrupt endpoint |
| 9.10 / 9.11 | random undefined descriptor types and feature selectors must STALL |
| 9.12 | remote wakeup: enabled must wake, disabled must not |
| 9.13 | SET/GET_CONFIGURATION for every configuration |
| 9.14 | suspend and resume, device still answers |
| 9.16 | repeated enumeration at random addresses with random resets |
| 9.17 / 9.18 | other-speed configuration and device qualifier must STALL (full-speed only) |
| 9.21 | LPM L1 (ends for bcdUSB ≤ 0x0200) |
| self-checks (7) | each checker catches a seeded bug: bad descriptor, missing STALL, unwanted wakeup, wrong toggle, corrupt IN data, late turnaround, wrong address |

`tb/uvm/README.md` has the details.

### Checker self-tests (pytest)

| File | Checks |
|---|---|
| `tb/cocotb/tests/test_usb_fs_codec.py` | PID encoding, CRC golden vectors, SYNC, malformed PID/CRC/stuffing/SE1/EOP detection |
| `tb/cocotb/tests/test_usb_fs_bus.py`, `test_usb_fs_timing.py`, `test_usb_fs_host.py` | the simulated host: bus capture, bit timing, data toggles, resets, timeouts |
| `tb/cocotb/tests/test_usb_packet_generator_scoreboard.py` | the generator scoreboard rejects bad SYNC, PID, payload, CRC and framing |
| `tb/cocotb/tests/test_usb_phy_tx_scoreboard.py` | the PHY checker rejects wrong pins, bit durations, encoding, OE dropouts and EOPs |
| `tb/cocotb/tests/test_usb_serializer_scoreboard.py` | the serializer monitor against an independent codec, plus negative controls |
| `tb/cocotb/tests/test_usb_ram_adapter_model.py` | the RAM adapter and endpoint models and their scoreboards |
| `tb/cocotb/tests/test_generated_registers.py` | the committed register block matches what `rdl/usb_device.rdl` generates (register map and RAM size cannot drift) |
| `tb/uvm/unit/*.py` | Chapter 9 device model, descriptors and SETUP encoding, random profiles against every USBCV check, HAL endpoint kinds, wire reconstruction |

## Feature coverage

| Feature | Covered by |
|---|---|
| NRZI, bit stuffing, SYNC, EOP | PHY RX/TX, serializer and deserializer tests; codec self-tests |
| CRC5 / CRC16 | `test_crc`, packet suites, generator tests |
| Bus reset detection | `test_reset_detected`, `test_usb_phy_rx_packets_random`, `bus_reset` |
| Turnaround timing | `test_usb_turnaround`, UVM protocol checker |
| Address and endpoint routing | `test_routing`, `test_foreign_packets`, `packet_routing`, TD 9.16 |
| Data toggles | `test_out`, `test_in`, `out_handshake`, `in_handshake`, UVM protocol checker |
| ACK / NAK / STALL | protocol tests, handshake firmware tests, TD 9.9 |
| SETUP handling | `test_setup_response`, `test_setup`, setup-stage tests, `setup` |
| Buffer ownership and sizes | endpoint tests, `buffer_size`, `ram_size` |
| Shared RAM size parameter | `ram_size`, `usb_ll_ram` and `cdc_echo`, passed at sizes from 1 to 64 KiB; `test_generated_registers` |
| Double buffering | `test_double_buffer_*`, `double_buffer`, `hal_double` |
| Split IN transfers | `test_in_split`, `test_double_buffer_in_split`, `in_split`, `hal_double` |
| Isochronous | `test_in_resend` (completion without ACK) |
| Suspend, resume, remote wakeup | `test_usb_bus_state`, `suspend`, TD 9.12, TD 9.14 |
| VBUS detection and pull-up gating | `vbus`, `cdc_echo` (unplug and replug re-enumerates) |
| Interrupts | `usb_interrupt`, every HAL/app test (interrupt-driven) |
| Clock-domain crossings | `test_cdc`, `test_device_address_crossing`, `test_control_crossing` |
| Chapter 9 behavior | UVM TD tests, `cdc_echo` |
| Throughput | `test_sw_cdc_bench_stream`, `tools/usb_bench.py` on hardware |

## Not verified

- **Electrical behavior:** signal quality, eye diagram, rise and fall times. These
  depend on the board and the FPGA I/O, not on the RTL.
- **Clock tolerance:** the receiver is not tested against host clock drift or bit
  jitter; all simulated traffic has ideal timing.
- **Formal proofs:** there are no formal proofs (FIFO bounds, CDC safety).
- **Gate-level and post-route simulation.**
- **Low-speed and high-speed:** out of scope for this full-speed core.
- **USB-IF certification:** the Chapter 9 tests are a re-implementation of the
  USBCV procedures, not the official tool.
