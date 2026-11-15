<p align="center"><img src="docs/figures/logo.svg" width="160" alt="usb-device logo"></p>

# USB 2.0 Full-Speed Device Controller for FPGAs

[![Data sheet](https://img.shields.io/badge/Data%20Sheet-HTML-0b5394?style=flat&logo=asciidoctor)](https://dpavlo55.github.io/usb-device/)
[![Data sheet PDF](https://img.shields.io/badge/Data%20Sheet-PDF-0b5394?style=flat&logo=adobeacrobatreader)](https://dpavlo55.github.io/usb-device/pdf/usb_device_datasheet.pdf)
[![Software reference](https://img.shields.io/badge/SW%20API-Doxygen-0b5394?style=flat&logo=doxygen)](https://dpavlo55.github.io/usb-device/sw/index.html)
[![Verification](https://github.com/dpavlo55/usb-device/actions/workflows/verification.yml/badge.svg)](https://github.com/dpavlo55/usb-device/actions/workflows/verification.yml)
[![Documentation](https://github.com/dpavlo55/usb-device/actions/workflows/docs.yml/badge.svg)](https://github.com/dpavlo55/usb-device/actions/workflows/docs.yml)
[![License](https://img.shields.io/badge/License-MIT-0b5394?style=flat)](LICENSE)

A USB 2.0 full-speed (12 Mbit/s) **device** controller written in portable
VHDL-2008, with a portable C device stack that runs on any 32-bit CPU that can reach
the core's AXI4-Lite port. The PHY is digital: D+ and D− connect to FPGA pins with a
1.5 kΩ pull-up on D+, and no external USB transceiver chip is needed.

The reference system uses the [NEORV32](https://github.com/stnolting/neorv32) RISC-V
soft core on a Digilent Arty A7-100, where it enumerates on Windows and Linux as a
CDC-ACM virtual COM port.

## Features

- **USB 2.0 full speed, device only.** Covers the digital PHY, the packet layer
  (NRZI, bit stuffing, CRC5/CRC16, PID checks) and transactions (handshakes, data
  toggles, STALL, turnaround).
- **16 endpoint entries.** Each entry has a configurable number, direction and type
  (control, bulk, interrupt or isochronous) and a buffer of 8 to 1024 bytes.
  - All buffers live in a shared RAM that the CPU maps directly. Its size is set
    by one parameter in the RDL: `RAM_ADDR_WIDTH`, from 1 KiB to 64 KiB (this repository
    sets 12, which gives 4 KiB).
  - Software reads the size from `status.ram_words`, and the HAL allocates buffers
    within it.
- **Optional double buffering per endpoint.** Two entries work as a ping-pong pair,
  so the host's packets are ACKed back to back while software empties the other
  buffer.
- **Split IN transfers.** One arm sends up to a whole buffer as several max-size
  packets, with one interrupt per transfer instead of per packet.
- **SETUP handling in hardware.** Data is written only after the packet is accepted,
  and control endpoints are unstalled automatically.
- **Bus states.** Suspend after 3 ms of idle bus, resume, remote wakeup with the
  spec's timing, SOF frame number, and VBUS detection: an interrupt on plug and
  unplug, with the D+ pull-up gated by VBUS in hardware.
- **AXI4-Lite register interface,** generated from SystemRDL. Also generated: a C
  header, an HTML register reference and a pyuvm register model.
- **Two clock domains:** a 48 MHz USB clock (4× oversampling) and any system clock.
- **Portable C device stack** in `sw/usb/`, in four layers: registers, HAL, device
  core (Chapter 9 standard requests) and classes (CDC-ACM).
  - Plain C11 with no CPU or SoC dependency; the base address is passed in at run
    time.
  - Porting takes four functions: interrupt enable/disable and critical-section
    enter/exit.
  - `sw/port/neorv32/` is the reference port.

## Status

The core is functionally complete for full-speed devices and is verified in
simulation:
- about 120 cocotb unit and integration tests
- 20 firmware-in-the-loop tests running on NEORV32 against a simulated host
- a pyuvm environment running the USB-IF **USBCV Chapter 9** device tests against
  the RTL

On hardware, it enumerates and transfers data reliably on Windows 11 and Linux
(WSL2 via usbipd). It has not been through USB-IF compliance testing or electrical
(signal quality) testing.

## Performance

These numbers are bulk CDC-ACM throughput on an Arty A7-100 with NEORV32 at 60 MHz
and no caches, measured with `tools/usb_bench.py`, 1 MiB × 3 runs, every byte
checked:

| Mode | Throughput | Share of the 1.216 MB/s bus ceiling |
|---|---|---|
| sink (host → device) | 0.94 MB/s | 78 % |
| sink, device checks every byte | 0.94 MB/s | 78 % |
| source (device → host) | 1.09 MB/s | 88 % |
| echo (both directions at once) | 0.57 MB/s each way | 94 % of the shared bus |

Bulk OUT throughput depends strongly on the host controller. One port on the test
PC capped OUT at 0.25 MB/s, while another reached 0.94 MB/s. In simulation, with a
host that never backs off, the device keeps up with the bus in both directions:
0 NAKs and 51 µs per 64-byte packet.

## Repository layout

| Path | Contents |
|---|---|
| `rtl/` | VHDL-2008 sources of the core; `usb_top.vhd` is the top level |
| `rdl/` | SystemRDL register description, the source of truth for the register map |
| `sw/` | the portable USB device stack (`sw/usb/`), CPU ports (`sw/port/`, NEORV32 provided), example apps (`sw/app/`) and firmware tests (`sw/tests/`) |
| `tb/` | Verification: cocotb tests, models and drivers (`tb/cocotb/`), VHDL harnesses (`tb/vhdl/`) and the pyuvm Chapter 9 testbench (`tb/uvm/`) |
| `tools/` | Regression runners and the hardware throughput benchmark |
| `docs/` | The data sheet (AsciiDoc, `docs/datasheet/`), the source of truth for architecture and registers, and the verification plan |
| `neorv32/` | NEORV32 as a git submodule |

## Getting started

Prerequisites:
- [NVC](https://github.com/nickg/nvc) 1.23 or newer
- Python 3.12
- GNU Make
- the xPack `riscv-none-elf-gcc` toolchain (for the firmware tests)
- a native `gcc`

```sh
git clone --recursive https://github.com/dpavlo55/usb-device.git
cd usb-device
python -m venv venv
venv/Scripts/activate            # Linux: source venv/bin/activate
pip install -r requirements.txt

python tools/cocotb_runner.py                     # top-level integration tests
python tools/cocotb_runner.py --protocol-only     # one unit harness (see --help)
python tools/cocotb_runner.py --sw-test cdc_echo  # firmware on NEORV32 against a simulated host
python tb/uvm/run.py                              # USBCV Chapter 9 tests (pyuvm)
```

`docs/verification-plan.md` lists every test and what it covers. To change a
register, edit `rdl/usb_device.rdl` and run `make -C rdl all`. That regenerates the
register block VHDL, the C header, the HTML reference and the pyuvm model.

## Documentation

The data sheet in `docs/datasheet/` describes the hardware, the register map, the programming
model, the software stack and the verification. `make -C docs html doxygen` builds it as HTML together
with the Doxygen software reference it links to (`docs/public/index.html`); `make -C docs pdf` builds the
PDF. It needs Asciidoctor (and asciidoctor-pdf for the PDF) and Doxygen. The figures are committed SVGs;
`make -C docs figures` regenerates them from `docs/figures/src/` (Python, and memmap for the memory maps).

## Using the device stack

`sw/app/cdc_echo` is a complete example: a virtual COM port that echoes every byte
back. `sw/README.md` describes the stack's layers, its configuration, interrupts,
VBUS handling, suspend and porting.

## License

MIT; see [LICENSE](LICENSE). NEORV32 is BSD-3-Clause and is included as a submodule.
