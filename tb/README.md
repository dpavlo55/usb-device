# Verification layout

`cocotb/` holds Python drivers, executable reference models, and test modules.
`vhdl/` holds simulator-only wrapper entities and VHDL assertions. The current
top (`usb_top_tb.vhd`) instantiates the DUT and exposes clocks, resets, and USB
pins as Cocotb-driven ports. Keep clock/reset generation and protocol stimulus
in Cocotb so individual scenarios can vary them independently. The wrapper
resolves `usb_dp_host`/`usb_dn_host` host drives with weak Full-Speed idle bias
and exposes the resolved bus as `usb_dp`/`usb_dn`.

The canonical runner loads all four RTL-facing tests:

- `test_usb_top.py` contains the unchanged `test_clock_reset_and_fs_traffic`
  directed smoke test for toolchain, reset, and pin-waveform behavior.
- `test_usb_phy_rx_random.py` drives one 64-byte DATA1 packet with seeded
  Python `random.Random` uniform bytes and verifies the DUT RX-PHY output with
  a byte-stream scoreboard. The scoreboard samples settled `clk_usb` values
  after `ReadOnly()` and compares the DATA PID, payload, and CRC16 bytes. After
  the packet is sent and the expected stream is complete, it includes a fixed
  post-packet drain interval for delayed PHY activity; it does not use an
  internal EOP signal as the pass condition.
- `test_usb_phy_rx_packets_random.py` drives 500–1000 valid packets per run. It
  randomizes DATA0/DATA1 payload lengths from 0–64 bytes, SETUP/OUT/IN tokens,
  SOF packets, and ACK/NAK/STALL handshakes. Each packet has an independent
  PID/payload/CRC byte-stream scoreboard and post-packet drain interval. It also
  inserts valid, variable-duration USB bus resets at randomized inter-packet
  boundaries.
- `test_usb_phy_rx_errors.py` injects a malformed SYNC in one packet and a
  malformed stuffed bit in a separate packet, checking that each corresponding
  RX-PHY error flag is asserted without the other error flag being asserted.
- `cocotb/tests/` contains pure Python driver, timing, and codec tests.

The packet-generator test (`test_usb_packet_generator.py`) instantiates the
generator in the standalone `usb_packet_generator_tb.vhd` harness and observes
its byte interface. This harness contains only the generator, with a single
Cocotb-driven `clk` and `rst_n`; `usb_top_tb.vhd` is unchanged. The
simulation-only scalar ports are converted to an initialized `packet_t` using
`to_pid()` and `to_packet_type()`; no VHDL enum ordinal is assumed in Python.
Stimulus and reset release use `RisingEdge` followed by `ReadWrite`: inputs
change after the DUT has evaluated the edge, and are sampled on the next
rising edge. The TX monitor samples settled outputs using `ReadOnly`.
The test pulses `packet.valid` for one clock and holds the remaining metadata
stable, presents the first DATA payload on the following cycle or up to 40
cycles later, then presents one payload byte per cycle with `payload_last` on
the final byte. `test_sync_and_pid_before_payload` checks that a DATA packet
starts on `packet.valid`: SYNC and PID leave while the payload is held back. Handshakes
and zero-length DATA packets have no payload strobes. There is no ready signal,
so the driver waits a fixed observation window (`4 * expected_bytes + 64`
cycles), followed by 16 trailing-output cycles, before requesting another
packet. Scoreboard errors are logged and collected without stopping stimulus;
checking an out-of-sync packet stops after its first error and resumes with
a fresh scoreboard on the next packet. Missing output is reported at the end
of its window. One final assertion fails the test after all packets are sent
if any scoreboard errors occurred. ACK/NAK/STALL and DATA0/DATA1 are exercised;
DATA payloads contain uniformly random bytes with lengths from 0–64. The
scoreboard expects only PID, payload, and CRC16 bytes (CRC low byte first): it
does not include USB SYNC, NRZI, bit stuffing, or EOP. `tx_last` is required
only with `tx_valid` on the final byte. `SEED` selects a replay seed;
otherwise fresh system entropy is used and the seed is included in failures.

Run this test with `python tools/cocotb_runner.py --generator-only` (add
`--no-waves` to disable waveform output). Generator builds and waveforms are
isolated in `sim/packet_generator_build/`; the waveform is
`usb_packet_generator_tb.fst`. Inspect this harness's `packet_pid`,
`packet_valid`, `payload_length`, `payload`, `payload_valid`, `payload_last`,
and `generator_packet` to see stimulus. Incomplete RTL can report missing or
malformed TX output; these errors do not stop later packets from being driven.
The default top-level regression does not load this test.
Generator waveform runs use NVC `--no-collapse` so the DUT's `packet` record
also remains visible under `usb_packet_generator_tb.packet_generator_inst`.

All seeded cocotb tests use the same `SEED` environment variable and accept
decimal or hexadecimal values. If unset, tests choose fresh 32-bit system
entropy, except the buffer test, which retains its fixed default of `0x1B00F`.
The elastic FIFO test also uses the fixed default `0x1B00F`.
The RX tests log the resolved value. Set `SEED` to replay a random RX test:

```powershell
$env:SEED = "24301"
python tools/cocotb_runner.py --no-waves --test-filter test_usb_phy_rx_random
```

The first RX test has no coverage-bin gate and deliberately uses only the
uniform byte generator. Nested `usb_top_inst.usb_phy_inst` handles are the
preferred PHY probe route. If a simulator cannot expose them, use only a
validated simulation-only observation port in `usb_top_tb.vhd`; never add debug
ports to production RTL.

## Packet serializer

Run the standalone suite from the repository root:

```powershell
python tools\cocotb_runner.py --serializer-only
```

Add `--no-waves` to disable waveform output. Use
`--test-filter test_without_backpressure` or `--test-filter test_with_backpressure`
to select one scenario. `SEED` accepts a decimal or hexadecimal replay seed;
using the same seed gives both scenarios the same packet corpus and order.
Build products are isolated in `sim/packet_serializer_build/`; the waveform is
`usb_packet_serializer_tb.fst`. The default top-level regression is unchanged.

The two tests verify pure byte-to-bit serialization through public AXI4-Stream
ports only. The VHDL wrapper directly exposes the 8-bit `packet_t*` input and
1-bit `stream_t*` output, including all sidebands and downstream `stream_tready`.
There is no bit stuffing, NRZI encoding, line-state checking, or generated EOP.

Both scenarios begin with a startup reset and send complete USB packets through
`cocotbext.axi.AxiStreamSource`: SYNC (`0x80`), PID, fields/payload, and CRC where
applicable. Only HANDSHAKE (ACK, NAK, STALL) and DATA (DATA0, DATA1) packets are
sent for serialization. DATA payloads include zero and one byte,
all 256 byte values, constant/alternating patterns, a 1023-byte payload, and
seeded random lengths and contents. Packet order is seeded but not logged;
the startup log reports the seed, backpressure setting, and packet count.
Input TKEEP is always 1 and TDEST is always 0. Each packet is observed to
completion or its per-packet timeout before the next packet is submitted.

Only bytes accepted on `packet_tvalid && packet_tready` enter the scoreboard.
Each byte must produce exactly eight LSB-first output transfers, with TKEEP=1
and TDEST=0. TLAST is asserted only on bit 7 of the final byte of each packet.
The scoreboard consumes bits only on `stream_tvalid && stream_tready` and
checks that TVALID and all output fields remain stable across stalls.
Input and output handshakes are sampled at the same rising transfer edge,
before HDL updates. Downstream readiness changes on falling edges.

`test_without_backpressure` holds `stream_tready=1` throughout.
`test_with_backpressure` applies deterministic multi-cycle stalls, including
byte/packet boundaries, and requires actual valid-output stalls to occur.
No fixed output latency or gap-free cadence is assumed. Each packet has its own
bounded completion window and must produce exactly eight transfers per byte,
including its final TLAST. Missing or incomplete output is logged with the
packet index, PID, and accepted-byte/serialized-bit counts. Monitoring continues for
eight quiet clocks after every packet to catch extra output before sending
the next packet.

The first checking error is logged immediately, but simulation continues for
128 additional clocks (`FAILURE_OBSERVATION_CYCLES`) before the original error
is re-raised. This extends the waveform beyond the failure without hiding it.
During this observation period, checking stops, no new packets are submitted,
the current source frame can continue, and `stream_tready` is held high to
allow output to progress. The DUT is not reset and later packets are not tested.

## USB PHY transmitter

`test_usb_phy_tx.py` tests `rtl/usb_phy_tx.vhd` through the standalone
`tb/vhdl/usb_phy_tx_tb.vhd` harness. The harness instantiates only the TX PHY,
fixes its stream widths to one bit, and exposes its ports to cocotb. Run it with:

```powershell
python tools/cocotb_runner.py --phy-tx-only
```

The runner reports a clear missing-RTL error until the module exists. Add
`--no-waves` to disable waveforms; otherwise they are written to
`sim/phy_tx_build/usb_phy_tx_tb.fst`. The default regression is unchanged.

The expected entity has `clk`, active-low `rst_n`, one-bit stream inputs
`serial_tdata`, `serial_tkeep`, `serial_tdest`, `serial_tlast`,
`serial_tvalid`, the output `serial_tready`, and outputs `usb_oe`, `usb_dp`,
`usb_dn`. Stream vectors may use `(0 downto 0)`, as on the packet serializer.
TKEEP is always 1 and TDEST is always 0. OE is active high.

The source sends complete HANDSHAKE (ACK/NAK/STALL) and DATA (DATA0/DATA1)
packets, including SYNC (`0x80`), PID and applicable payload/CRC16 bytes,
LSB-first. TLAST marks the final CRC or PID bit. It holds every beat stable
until READY accepts it and offers the next bit without a deliberate bubble.
The PHY must insert stuffed zeros, NRZI-encode starting from full-speed J,
generate EOP, and drive D+/D- while OE is high. Packet boundaries reset the
stuffing count and NRZI state. No fixed input-to-output latency is required.

`UsbPhyTxScoreboard` builds its expectations only from accepted stream bits.
The timing contract is four clock samples per USB bit at the project's
approximately 48 MHz clock. Every sample must match the encoded line state,
including stuffed cells and EOP (eight SE0 clocks followed by four J clocks).
OE must remain asserted through the entire packet and drop after EOP.
The scoreboard ignores D+/D- while OE is low, requires OE low during reset,
and checks idle periods for unexpected drive. On every rising edge, the driver
samples the previously presented beat and READY before HDL clocked updates,
then drives reset and the next stream beat in `ReadWrite` for the next rising
edge. Stalled beats stay valid and unchanged. Output pins are checked after
updates using `ReadOnly`. No stimulus changes on falling edges.
The check uses USB cell timing independently of input TVALID/READY cadence,
so internal buffering and stalls for stuffing do not change the expected rate.

Coverage includes empty DATA payloads, both DATA PIDs, all byte values,
zero/one/alternating patterns, stuffing across byte boundaries, a valid CRC
ending with six ones (stuffing immediately before EOP), 1023-byte payloads,
and 24 seeded random packets. `SEED` selects a decimal or hexadecimal replay
seed. Every packet has a bounded timeout and 16 trailing clocks to detect
extra output. Failures identify the seed, packet, cell, and sample phase.

Additional TX tests queue consecutive packets without source gaps to check
that EOP does not consume the next packet's bits. Reset is also asserted during
data and each EOP cell, with buffered traffic discarded and fresh traffic
verified after reset.

The pure Python scoreboard tests run without TX RTL:

```powershell
python -m pytest tb/cocotb/tests/test_usb_phy_tx_scoreboard.py -p no:cacheprovider
```

These include deliberate wrong NRZI/stuffing, corrupt pins at each of the
four sample phases, shortened/lengthened cells, premature OE release,
malformed EOP, missing/extra output, and streaming input acceptance.

## Elastic FIFO

Run `python tools/elastic_fifo_runner.py --no-waves` to execute the standalone
FIFO tests at `ADDR_WIDTH=1, 2, 3, 4, 5`, corresponding to memory depths
`2, 4, 8, 16, 32` (`2**ADDR_WIDTH`) plus one input-buffer and one output-buffer
slot. Only `ADDR_WIDTH` is overridden;
`DATA_WIDTH`, `KEEP_WIDTH`, and `DEST_WIDTH` retain the RTL defaults.
Use `--addr-width 3` (or the memory-depth shortcut `--depth 8`) to select one case,
`--test-filter reset` to select a test, and `SEED` to replay random
traffic. Waveforms are enabled unless `--no-waves` is supplied. Each depth's
build, waveform, and results are isolated in `sim/elastic_fifo_build/depth_N/`.
The runner supplies `ELASTIC_FIFO_ADDR_WIDTH` to the test's capacity model;
when running without this runner, set it to match the DUT generic (default 2).

The independent handshake queue compares all four payload fields, checks exact
capacity, empty/full transitions, stalled output stability, repeated pointer
wraparound, sustained simultaneous push/pop, all default-width sideband
combinations, all 256 byte values, and 2,000 randomized transfers per depth.
`LAST` and `KEEP` are metadata: even `KEEP=0, LAST=1` must be retained. Source
beats stay valid and unchanged until accepted. Both conservative full-FIFO
ready and ready that permits simultaneous replacement at full occupancy are
accepted; output is expected to show the stored head without bypass or bubbles.

Reset is asynchronous and active-low. At every occupancy from zero to capacity,
tests assert reset between clock edges with live input/output controls and
require `OVALID=0` **before** another rising edge. Reset must discard pending
beats, including after pointer movement; fresh traffic must work afterwards.
Short reset pulses entirely between rising edges are checked at every occupancy.
Invalid output payload values are unspecified and are not checked.

Organize tests by feature, not implementation file. A test must exercise the
public behavior of a block and use a reference model where a calculated result
(CRC, NRZI, packet encoding, register result) is expected.

## USB-side shared RAM adapter

`test_usb_ram_usb_adapter.py` checks `usb_ram_usb_adapter` as a black box in the
standalone `usb_ram_usb_adapter_tb.vhd` harness (adapter plus `usb_ram`). The
adapter only moves streams to and from the RAM; ownership and configuration
belong to `usb_endpoints_controller`. The pure reference model and scoreboards
are in `models/usb_ram_adapter_model.py` (unit-tested in
`tests/test_usb_ram_adapter_model.py`). The testbench drives the per-endpoint
`base_address`/`write_allowed`, `rx_payload` (OUT) and the one-cycle
`read_command.start` pulse (IN). It monitors `tx_payload` (including the rule
that a stalled beat must not change or disappear), the OUT report (one per
packet: endpoint, byte count, accepted) and the IN report, which must appear in
exactly the cycle the last IN beat is taken. The RAM's SW port is a backdoor:
the RAM is preloaded with random bytes and compared in full with the model at
the end of every test. `rx`/`tx` valid and ready are throttled with `always`,
`random`, `bursty` and `sparse` patterns, and endpoint regions use unaligned
base addresses on all 16 entries.

Covered: OUT and IN packets on every entry, OUT packets without write
permission (dropped, reported as not accepted), write permission sampled at the
first beat (changes later in the packet have no effect), the OUT report arriving
in time for a registered controller to block the very next back-to-back packet,
concurrent OUT and IN with data written in one phase read back in the next,
strict OUT priority, and zero-length packets in both directions. IN timeouts do
not count cycles in which OUT beats are pending, because strict priority lets
OUT starve IN.

Run with `python tools/cocotb_runner.py --ram-usb-adapter-only` (`SEED` replays a run).

## Clock-domain-crossing primitives

`test_cdc.py` checks `cdc_pulse` and `cdc_handshake` in `cdc_tb.vhd` at five
clock ratios (faster, slower, much faster, much slower and equal). `cdc_pulse`:
each spaced source pulse gives exactly one one-cycle destination pulse within a
bounded latency. `cdc_handshake`: items cross once and in order under
`always`/`random`/`sparse` valid patterns, `dst_valid` is one cycle wide,
`dst_data` holds between items, `src_ready` never accepts a new item before the
previous one was delivered, and latency is bounded.

Run with `python tools/cocotb_runner.py --cdc-only`.

## Endpoint subsystem (ownership and transfer events)

`test_usb_endpoints.py` checks `usb_cdc` plus `usb_endpoints` (receive filter,
SETUP stage, controller, adapter, RAM) in `usb_endpoints_tb.vhd` with both clocks. The `clk_sys` side is
driven the way the register block would: core enable, endpoint configuration,
transfer length with a one-cycle `arm`, the RAM's SW port; transfer events are
observed there too. The rules live in `models/usb_endpoints_model.py`: an armed
OUT endpoint takes one packet (event with byte count, `zlp` for a zero-length
packet), an OUT packet for an unarmed endpoint is dropped with an `overrun`
event, an armed IN endpoint sends `length` bytes once per arm, and disabled
endpoints, wrong directions and a disabled core do nothing. Tests leave
realistic gaps between packets and settle after software actions; main tests
run with a 60 MHz and a 20 MHz `clk_sys` against the 48 MHz `clk_usb`.

Run with `python tools/cocotb_runner.py --endpoints-only`.

`usb_endpoints` itself is wiring only. Its two receive blocks have their own
harnesses:

- `--rx-filter-only`: `test_usb_rx_filter.py` on `usb_rx_filter_tb.vhd`. The
  route on `rx_route` in the cycle a packet's first beat is taken applies to the
  whole packet; a packet that is not accepted is dropped; an accepted one comes
  out with `dest = setup & entry` (5 bits) and keep cleared past the entry's
  buffer (2 ** (size + 3) bytes).
- `--setup-stage-only`: `test_usb_setup_stage.py` on `usb_setup_stage_tb.vhd`.
  Packets without the SETUP flag pass unchanged. A SETUP packet's first 8 bytes
  are held and written as an 8-beat packet only after `setup_received`, between
  packets and after the previous SETUP write was reported. `setup_writing` covers
  exactly those beats at the output and `setup_written` pulses once the 8th beat
  has been taken.

Both run every throttle combination on input and output and were checked by
injecting bugs (each one has to fail a test).

The register mapping (interrupts, `status.armed`, `status.byte_count`) is
checked end to end by the `endpoint_transfer` firmware test: run
`python tools/cocotb_runner.py --sw-test endpoint_transfer`. Its host side is
`test_sw_endpoint_transfer.py`; a firmware test with a `test_sw_<name>.py`
module runs that module instead of `test_usb_sw.py`.

The turnaround harness (`usb_turnaround_tb.vhd`) chains the full RX path
(input synchronizers, PHY RX, deserializer, parser) to the full TX path
(generator, serializer, PHY TX) through a one-cycle registered responder that
stands in for the usb_protocol decision: host DATA gets ACK, an IN token gets
DATA1 whose payload arrives 2 or 40 cycles later, like a RAM read. The IN
turnaround must not depend on that latency. The receiver hears the shared
bus, blocked by `usb_oe` through `usb_phy_rx`'s `tx_active`; the test checks
that the device sends exactly one packet per host transaction, so it never
answers its own transmission. `test_usb_turnaround.py` sends OUT, SETUP and IN
transactions at random sub-bit phases and measures, on the bus, the time from
the SE0-to-J transition ending the host's EOP to the first state the device
drives. It asserts the USB 2.0 limits of 2 to 6.5 bit times and logs the
spread per case, split into RX clocks (EOP end to the parser's packet record)
and TX clocks (response record to the first driven K).

Run with `python tools/cocotb_runner.py --turnaround-only`.

The host model receives packets the way a real receiver does: with a cocotb
port it waits for the SOP's K edge, moves half a bit on, and samples every
following cell mid-bit (`UsbFsBus.wait_for_sop`, `capture_cells`).

`usb_phy_rx` reports the idle J state while its `tx_active` input is high, so
the receiver ignores the device's own transmissions; `usb_top` drives it from
`usb_oe`. No guard time follows: the device's packet ends with a driven J, and
a host may answer two bit times after the device's EOP.
`test_usb_packet_deserializer.test_tx_active_hides_the_bus` checks that packets
on the line during `tx_active` produce no bytes and no errors, and that a packet
right after it is received.

`usb_packet_deserializer` outputs `reset_detected`: high once SE0 has lasted
2.5 us (120 `clk_usb` cycles) and until SE0 ends. `usb_top` synchronizes it
into `clk_sys`, where it sets `bus_event.bus_reset` and clears
`device_address`. `test_usb_packet_deserializer.test_reset_detected` checks
that an EOP and SE0 shorter than 2.5 us never raise it, that a 3 us SE0 raises
it once and holds it until SE0 ends, and that a packet right after a reset is
received. The `bus_reset` firmware test (`--sw-test bus_reset`, host side
`test_sw_bus_reset.py`) checks the register effects end to end.

## usb_protocol skeleton

`usb_protocol` decides which endpoint entry, if any, receives the next DATA
payload. On every packet record with a good PID and CRC, an OUT or SETUP token
addressed to `device_address` selects the lowest enabled OUT entry serving the
token's endpoint (SETUP only a CONTROL entry) and sets `rx_route.accept`; any
other packet and a bus reset clear it. `usb_rx_filter` (in `usb_endpoints`) takes
`rx_route` with the first beat of each payload: it routes the packet to that entry (`tdest` is
ignored) or, when not accepted, consumes and drops it without a report or event.
`device_address` reaches `clk_usb` through a `cdc_handshake` in `usb_cdc`.

- `--protocol-only`: `test_usb_protocol.py` on `usb_protocol_tb.vhd`, directed
  cases plus random packets against a Python model of the rules.
- `--endpoints-only`: `test_foreign_packets` (not accepted: nothing written, no
  event) and `test_device_address_crossing`; the other tests route by `rx_route`.
- `--sw-test packet_routing`: two OUT entries at address 5; DATA after a token to
  another address or after an IN token is not written, OUT DATA lands in the
  entry serving its endpoint. `endpoint_transfer` now sends OUT tokens first.

### SETUP

A SETUP token sets `rx_route.setup`. While its DATA0 packet arrives,
`usb_setup_stage` holds the first 8 bytes and writes nothing. When the packet
record shows a good PID and CRC and exactly 8 bytes, `usb_protocol` answers ACK
on `tx_packet` (also while the entry is stalled) and pulses `setup_received`; the
held bytes are then written to the start of the entry's buffer, even while the
entry is not armed. A rejected SETUP writes nothing. After the write,
`usb_endpoints` disarms every entry
serving the same endpoint number and sends an event with `setup = 1` and
`byte_count = 8`; `usb_top` turns it into `interrupt.setup` and `byte_count`, and
clears `armed` and `stall` on those entries.

- `--protocol-only`: `test_setup_response` (ACK only for DATA0, good CRC and PID,
  8 bytes after an accepted SETUP), and the random test checks the ACKs too.
- `--endpoints-only`: `test_setup`.
- `--sw-test setup`: the host must receive the ACK on the pins, and no answer for
  a bad CRC, DATA1 or 7 bytes; the firmware checks the registers and the bytes.

### OUT

`usb_protocol` decides at the OUT token from the entry's state, which it
latches: stalled, not armed, or armed (`armed` comes from `usb_endpoints`,
`stall` reaches `clk_usb` through a synchronizer in `usb_cdc`). `rx_route.accept`
is set only when the payload is to be written. At the DATA record it answers:
nothing for a bad PID or CRC; STALL; NAK with `out_result.overrun`; or ACK, and
with the expected data PID it flips the entry's toggle and reports
`out_result.complete` with the packet's `payload_length`. Isochronous entries get
no handshake and no toggle check. `usb_endpoints` completes OUT transfers from
`out_result`; the RAM adapter's end-of-packet report is no longer used. Toggles
live in `usb_protocol`: DATA0 after reset, a bus reset and `transfer.reset_toggle`
(a pulse through `usb_cdc`), DATA1 for both entries of an endpoint after SETUP.

- `--protocol-only`: `test_out`, and the random test covers OUT, toggles and
  `reset_toggle` against the model.
- `--endpoints-only`: the environment drives `out_result` the way `usb_protocol`
  does; `test_control_crossing` checks `stall` and `reset_toggle`, and every test
  compares `armed` with the model.
- `--sw-test out_handshake`: ACK and completion, NAK and overrun, a stale DATA0
  ACKed and dropped, STALL, no answer to a bad CRC, and `reset_toggle`. Hosts now
  wait for the device's handshake before the next token (`endpoint_transfer`,
  `packet_routing`).

### IN

An IN token for the lowest enabled IN entry serving its endpoint is answered
from the entry's state: STALL, NAK, or DATA0/DATA1 by the toggle. With DATA,
`usb_protocol` starts the RAM read in the same cycle (`in_request`, the
endpoints' descriptor) and waits for the next received packet: an ACK flips the
toggle and pulses `in_result.complete`, which hands the buffer back; anything
else leaves the entry armed, and the next IN sends the same data with the same
PID. Isochronous IN entries send DATA0 and complete at the end of the read, from
the RAM adapter's report.

- `--protocol-only`: `test_in`, and the random test covers IN and ACKs.
- `--endpoints-only`: the environment pulses `in_result` after the IN data, as
  `usb_protocol` does after the ACK; `test_in_resend` covers the missing ACK and
  isochronous entries.
- `--sw-test in_handshake`: DATA0 and ACK, NAK, a missing ACK and the same DATA1
  again, a zero-length packet, STALL, and `reset_toggle`.

### Double buffering

`control.double_buffer` on entry k (k < 15, not CONTROL) makes entry k+1 the
second buffer of the same endpoint. `usb_protocol` never matches a token to entry
k+1 and serves each token of entry k from entry `k + next[k]`: that entry's
`armed` and `size`, entry k's `stall`, `kind` and toggle. A completed transfer
flips `next[k]`; an unarmed turn is NAKed (overrun on that entry) and never skips
to the other buffer. `next` clears on a bus reset and while entry k is disabled or
unpaired, not on `reset_toggle`. `status.next_buffer` follows the same rule from
the events in `clk_sys`.

- `--protocol-only`: `test_double_buffer_out`, `test_double_buffer_in`,
  `test_double_buffer_ignored`, and pairs in the random test.
- `--sw-test double_buffer`: back-to-back OUT packets into both buffers, NAK and
  overrun while the unarmed buffer has the turn, `status.next_buffer`, and IN data
  alternating between the buffers.
- `--sw-test hal_double`: `usb_hal_ep_open_double()`, two raw packets armed per
  endpoint, completions reported in arrival order (also when entry k+1 filled
  first), and transfers one packet at a time.

### Split IN transfers

`control.split` on an IN entry sends a transfer as packets of
`2 ** (size + 3 - split)` bytes: the controller keeps `sent` per entry, reads the
next `min(remaining, packet)` bytes from `base_address + sent` on each request,
advances on each ACK (or isochronous read end) and completes the transfer only on a
short packet or the one that sends the last byte. A transfer of whole packets ends
without a zero-length packet. `in_last` (remaining fits one packet) tells
`usb_protocol` when an ACK ends the transfer, so a double-buffered endpoint flips
`next_buffer` only then.

- `--endpoints-only`: `test_in_split` (random sizes, splits, lengths, missing ACKs
  and isochronous entries against the model, and `in_last`).
- `--protocol-only`: `test_double_buffer_in_split`.
- `--sw-test in_split`: 100 bytes as 64 + 36, 128 bytes as 64 + 64 with no
  zero-length packet, and two double-buffered 128-byte transfers in a row.
- `--sw-test hal_double`: `usb_hal_ep_open_ex()` with 256-byte transfers, raw arms
  and a transfer with ZLP.

The host model waits two bit times after each device packet before it
transmits (`INTER_PACKET_DELAY_BITS` in `usb_fs_host.py`), as USB requires;
without it the host's ACK started while the device was still driving its EOP.
Firmware tests that hand phases over with pull-up toggles use
`test_usb_sw.PullUpToggles`, which counts toggles from the start of the test, so a
host still busy on the bus cannot miss one.

### Interrupt

`usb_top` drives `irq` (in `clk_sys`) from the register block's
`hwif_out.interrupt.intr`: high while a bit of the global `interrupt` register is
set and enabled in `interrupt_enable`. The `interrupt` register itself shows every
pending cause whatever `interrupt_enable` holds. In `usb_top_tb`, `irq` is the CPU's
machine external interrupt (`irq_mei_i`) and is visible as `usb_irq`.
`--sw-test usb_interrupt` checks that a masked bus event leaves `irq` and `mip.MEIP`
low, and that an enabled bus reset and an enabled SETUP raise both until the
firmware clears the cause.
