# Simulation output

`tools/cocotb_runner.py` writes transient NVC build products, the FST waveform
(`sim/build/usb_top_tb.fst`), and cocotb xUnit results to `sim/build/`; this
directory is ignored by Git. The Python runner is the supported local entry
point because it uses cocotb's runner API directly and therefore works with the
installed Windows NVC toolchain. Top-level simulations first build `sw/main.c`
using GNU Make, the RISC-V GCC toolchain, and native GCC. Standalone block modes
do not build firmware. The canonical runner builds once and runs the complete
Cocotb regression.

For the firmware run alone, run
`python tools/cocotb_runner.py --firmware-only`. It compiles
`sw/neorv32_imem_image.vhd` directly and prints the values read at `0xF0000000`
and `0xF0000004` through UART0 simulation mode. It does not compare these values
or the console text against expectations. See `sw/README.md` for the
software build layout. Full simulator output is saved to
`sim/build/firmware.log` in this mode.

Run the full regression without waves with:

```powershell
python tools/cocotb_runner.py --no-waves
```

Run only the packet parser suites (token, handshake, special, mixed, and DATA)
with:

```powershell
python tools/cocotb_runner.py --no-waves --packet-only
```

To check the parser's payload AXI4-Stream backpressure behavior in its standalone
harness, run:

```powershell
python tools/cocotb_runner.py --no-waves --parser-only
```

The DATA parser test covers DATA0 and DATA1 with payload lengths up to 64 bytes.
It currently exposes the parser's missing DATA-packet support and is expected to
fail until that RTL support is implemented.

Select tests by name within the selected regression with:

```powershell
python tools/cocotb_runner.py --no-waves --test-filter test_usb_phy_rx_random
```

Omit `--no-waves` for the default FST output. Keep a single canonical runner so
local development and continuous integration execute the same compilation order
and test selection. The PHY DUT and its incremental tests are real verification
evidence, while controller RTL and its higher-level tests will grow separately.

The standalone packet-generator harness uses the same runner with
`python tools/cocotb_runner.py --generator-only`. It compiles the USB package,
CRC unit, generator RTL, and `usb_packet_generator_tb.vhd`, and writes results
to `sim/packet_generator_build/`. The default waveform is
`sim/packet_generator_build/usb_packet_generator_tb.fst`; add `--no-waves` to
disable it. This mode is separate from the default `usb_top_tb` regression and
cannot be combined with `--packet-only`.
