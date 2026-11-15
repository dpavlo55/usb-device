# pyuvm USB 2.0 Chapter 9 compliance testbench

A [pyuvm](https://github.com/pyuvm/pyuvm) environment that runs the USB-IF Command
Verifier **Chapter 9 (Device) tests** (USB 2.0 CV specification, section 3.1) against the
full-speed device core, with constrained-random device profiles (pyvsc), functional
coverage (pyvsc covergroups, UCIS XML), a scoreboard, a protocol checker and a
spec-derived Chapter 9 state model.

```powershell
.\venv\Scripts\Activate.ps1
python tb/uvm/run.py                          # every TD test against the RTL
python tb/uvm/run.py --dut reference          # against the behavioral reference device
python tb/uvm/run.py --td 9.4 9.9 --seed 0x1234 --timing spec --waves
python tb/uvm/run.py --dut reference --selfcheck   # prove the checkers catch bugs
python -m pytest tb/uvm/unit -p no:cacheprovider   # pure-Python unit tests (no simulator)
```

`SEED` (or `--seed`) replays a run; without it a fresh seed is drawn and logged. Builds,
logs and coverage databases (`coverage_<test>.xml`) go to `sim/uvm_build/<dut>/`.

## Test map

| TD | Test | File | Notes |
|---|---|---|---|
| 9.1 | Device Descriptor | `tests/test_ch9_TD_9_1.py` | idVendor vs the USB-IF list is not checked (list unavailable) |
| 9.2 | Configuration Descriptor | `tests/test_ch9_TD_9_2.py` | |
| 9.3 | Interface Association | `tests/test_ch9_TD_9_3.py` | from the USB 3.2 CV spec v1.22; device constrained to have IADs |
| 9.4 | Interface Descriptor | `tests/test_ch9_TD_9_4.py` | device constrained to have alternate settings |
| 9.5 | Endpoint Descriptor | `tests/test_ch9_TD_9_5.py` | CV_FULL_1X / CV_FULL_2X tables |
| 9.7 | BOS Descriptor | `tests/test_ch9_TD_9_7.py` | bcdUSB <= 0x0200 must STALL; full BOS checks otherwise |
| 9.8 | String Descriptor | `tests/test_ch9_TD_9_8.py` | every index, every LANGID |
| 9.9 | Halt Endpoint | `tests/test_ch9_TD_9_9.py` | plus a STALL probe of the halted endpoint |
| 9.10 | Bad Descriptor | `tests/test_ch9_TD_9_10.py` | constrained-random undefined descriptor types |
| 9.11 | Bad Feature | `tests/test_ch9_TD_9_11.py` | constrained-random undefined feature selectors |
| 9.12 | Remote Wakeup | `tests/test_ch9_TD_9_12.py` | enabled must wake, disabled must not |
| 9.13 | Set Configuration | `tests/test_ch9_TD_9_13.py` | |
| 9.14 | Suspend/Resume | `tests/test_ch9_TD_9_14.py` | also checks the device software got the suspend and resume events |
| 9.16 | Enumeration | `tests/test_ch9_TD_9_16.py` | random count, random addresses, random device resets, stale-address probe |
| 9.17 | Other Speed Configuration | `tests/test_ch9_TD_9_17.py` | full-speed-only: must STALL (USB 2.0 section 9.6.2) |
| 9.18 | Device Qualifier | `tests/test_ch9_TD_9_18.py` | full-speed-only: must STALL (USB 2.0 section 9.6.2) |
| 9.21 | LPM L1 Suspend Resume | `tests/test_ch9_TD_9_21.py` | ends for bcdUSB <= 0x0200; L1 needs an LPM host port |
| 9.30 | Configuration Summary | `tests/test_ch9_TD_9_30.py` | skipped for bcdUSB <= 0x0200, as the spec says |

Not applicable to USB 2.0 devices (the specification says so): TD 9.6, 9.15, 9.19, 9.20,
9.22 to 9.29.

USBCV's waits of seconds become short host timings unless `--timing spec` is used; the
timers the *device* must implement (3 ms suspend, 5 ms before remote wakeup, 1-15 ms of
remote-wakeup K) are never shortened. See `usbtb/usb/timing.py`.

## Architecture

```
tests/                 one file per TD: a test class + its procedure (virtual sequence)
usbtb/
  clkrst/              clock/reset UVC: one agent per domain, power-on and reset pulses as sequences;
                       reset active low/high, sync/async assertion and release, rising/falling edge
  axil/                AXI4-Lite UVC on cocotbext-axi; register adapter and predictor
  ral/                 register model generated from rdl/usb_device.rdl (peakrdl-pyuvm)
  usb/                 USB host UVC: wire layer, host driver (frames, transactions, bus states),
                       bus monitor, transaction/control-transfer reconstruction, timing
  ch9/                 Chapter 9 domain model (pure Python): requests, descriptors,
                       random device profiles, device request handling, USBCV checks
  device/              the device software (control transfers, endpoint programming) and the
                       HAL it runs on: the RAL (RTL) or the behavioral reference core
  env/                 environment, configuration, scoreboard, protocol checker,
                       Chapter 9 state tracker, coverage
  seq/                 host facade, control-transfer sequences, Chapter 9 base sequence and
                       the step blocks shared between TDs, random invalid requests
  common/              reset awareness, seeding, compliance log, TLM helpers
hdl/                   harnesses: usb_top_uvm_tb (RTL), usb_reference_tb (bus only)
unit/                  pytest unit tests of the pure-Python layers
```

**The device under test is hardware plus software.** Chapter 9 requests are answered by
software on the CPU. Here the CPU is replaced by the *device software* model
(`usbtb/device/software.py`), which works the core's registers through the RAL and the
AXI4-Lite agent: it reacts to SETUP and transfer-complete events, serves the descriptors
of a random device profile, and programs endpoints for each configuration. The same
software runs on a behavioral model of the core (`ReferenceHal`), which lets the whole
suite run, and be checked, before the RTL exists.

**What checks what:**

* the TD procedures check what USBCV checks, from the device's answers only;
* the **scoreboard** checks the hardware against what the software asked for: IN data on
  the wire equals the armed buffer, OUT and SETUP bytes reach software unchanged, stalls
  happen exactly when requested, the device answers only its own address;
* the **protocol checker** checks packets: integrity, answer types per token (SETUP only
  ACK), turnaround (7.5 bit times), endpoint 0 data toggles;
* the **Chapter 9 state tracker** follows the device state from the bus alone and checks
  every state-dependent answer (GET_CONFIGURATION, GET_INTERFACE, GET_STATUS, Address-state
  request errors) in every test;
* `--selfcheck` injects one bug at a time (wrong toggle, corrupted data, slow answer,
  no address filter, missing STALL, bad descriptor, wakeup while disabled) and requires the
  matching checker to fire.

**Reset awareness:** the clock/reset monitors publish each domain's reset into a
`ResetSignal`; components mixing in `ResetAware` cancel their activity, flush state and
restart when reset asserts (the AXI driver also drops accesses requested before the reset).
TD 9.16 resets the device in the middle of the test.

**Randomization (pyvsc):** the device profile (`usbtb/ch9/profile.py`: endpoint 0 size,
configurations, interfaces, alternate settings, endpoint types/sizes/intervals, IADs,
strings and languages, power, remote wakeup) within the hardware's 14 free endpoint
entries and 4 KiB RAM; invalid descriptor types and feature selectors; device addresses;
the device software's reaction time (seen on the bus as NAKs); host turnaround. A test
may constrain the profile (`profile_filter`, e.g. TD 9.12 needs remote wakeup).

**Coverage (pyvsc covergroups, `usbtb/env/coverage.py`):** standard requests x device
state x outcome, descriptor reads (type, reply length vs wLength, packet count, ZLP
termination), feature requests, addresses, NAKs per transfer, bus events, profile shape.

## Register contract

The core's registers are defined in `rdl/usb_device.rdl`. The tests assume the additions
made there for the transaction layer: `device_address` (cleared by hardware on bus
reset), `control.remote_wakeup`, `status.suspended`, `bus_event`/`bus_event_enable`
(reset, suspend, resume, SOF), and per endpoint `control.endpoint_number`,
`control.stall` and `transfer.reset_toggle`. SETUP bytes are read from the start of the
control OUT entry's buffer in the shared RAM; there are no SETUP registers. Firmware
never writes the address back to `0` after a bus reset, so the tests rely on hardware
clearing it. `run.py` regenerates the register model from the RDL before each
run (also `make -C rdl pyuvm`). Until `rtl/usb_device_regs.vhd` is regenerated and the
transaction layer exists, the RTL runs stop at the first missing register.

Against the RTL, `RegisterHal` waits for usb_top's `irq` whenever the `interrupt` register reads 0, instead of polling it over AXI4-Lite; the reference DUT, which has no `irq`, is still polled.

The host agent leaves 2 to 4 bit times of idle bus after every device packet before its next token or SOF (`UsbHostConfig.inter_packet_delay_bits`, USB 2.0 7.1.18.1); the RTL ignores the bus while it drives its own EOP. `RegisterHal` writes endpoint kinds in the RDL's `endpoint_kind_e` encoding (`hal.RDL_ENDPOINT_KIND`), which differs from the descriptor encoding of `TransferType`; `unit/test_register_hal_kind.py` checks the table against the RDL.
