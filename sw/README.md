# NeoRV32 firmware

## Software tests

Each software test has its own `sw/tests/<name>/main.c`. Run the endpoint
RAM test from the repository root with:

```powershell
.\venv\Scripts\python.exe tools/cocotb_runner.py --firmware-only --sw-test endpoint_ram_sw --test-filter test_sw_firmware --no-waves
```

The runner builds the selected program and its IMEM image under
`sw/build/tests/<name>/`, then runs the generic cocotb test using a separate
simulator directory under `sim/firmware_tests/<name>/`. `--test-filter` selects
a cocotb test; `--sw-test` selects the firmware. Names may contain letters,
digits, and underscores. Omitting `--sw-test` retains the normal application
and firmware smoke test. With waveforms enabled, each software test also writes
its waveform to `sim/build/usb_top_tb.fst`, replacing the previous waveform so
it can be reloaded in the viewer. `--no-waves` leaves that file unchanged.

To boot a software test while running USB packet stimulus in the same simulation:

```powershell
.\venv\Scripts\python.exe tools/cocotb_runner.py --sw-test endpoint_ram_hw --test-filter test_usb_data_packets --no-waves
```

The selected `main.c` runs on the CPU while the cocotb test drives the USB bus.
The DATA packet test checks both its packet assertions and the firmware result;
it fails if the firmware reports an error or does not finish within 5 ms. Other
cocotb tests need their own firmware-result check if their result depends on it.
`--firmware-only` restricts selection to the generic firmware test. Without a
filter, `--sw-test` also runs that generic test by default. Software images
cannot be used with standalone block harnesses.

Tests can include `../common/test_status.h` and call `test_finish(1)` for
success or `test_finish(code)` with a code greater than 1 for failure. The
status register is implemented only in the simulation testbench at
`0xF0100000`, outside the USB window at any RAM size. The generic firmware and DATA packet tests fail if no status
arrives within 5 ms. The endpoint RAM
test writes each word's index across the RAM size reported by `status_ram`, then
reads and compares every word. Its failure code is the first mismatching word
index plus 2. It enables the USB pull-up after detecting VBUS so it can also
run alongside USB packet stimulus.

`sw/tests/endpoint_ram_hw/main.c` can be used as a hardware-oriented test.
It must report a result with `test_finish` after its test logic completes.

## USB device library

`sw/usb/` is a USB 2.0 full-speed device stack for this core. It has no NEORV32
dependency: the CPU-specific part is one small port file. Every program built by
the Makefile links it (`usb/src/*.c` plus `port/neorv32/*.c`, compiled with `-O2`).

| Layer | Files | Role |
|-------|-------|------|
| L1 registers | `usb_ll.h`, `usb_ram.h`/`.c` | Inline register accessors on the generated `usb_device_regs.h`; word-only copies to and from the shared USB RAM (4-word unrolled when aligned) |
| L2 HAL | `usb_hal.h`/`.c` | Endpoint entries and RAM buffers, multi-packet transfers with optional ZLP, stall, zero-copy access (`usb_hal_ep_buffer`, `usb_hal_ep_arm_raw`), events: bus reset, suspend, resume, SETUP, transfer done |
| L3 device | `usbd.h`, `usbd_core.c`, `usbd_ctrl.c`, `usbd_std.c` | Device state, EP0 control transfers, standard requests, descriptors (ASCII strings sent as UTF-16), request routing to class drivers |
| L4 classes | `usbd_class.h`, `usbd_cdc_acm.h`/`.c` | Class driver interface; CDC-ACM virtual COM port with RX/TX rings |
| Port | `usb_port.h`, `port/neorv32/usb_port_neorv32.c` | Mask/unmask the USB interrupt; critical sections |

All configuration has defaults in `usb_config_default.h`; an application overrides
them in its own `usb_config.h` (found on the include path).

**API docs.** `make -C sw docs` runs Doxygen with `sw/Doxyfile` (call and caller
graphs need Graphviz) and writes `sw/build/doxygen/html/index.html`. Pass
`DOXYGEN=<path to doxygen.exe>` when it is not on PATH.

**Interrupts.** All USB work happens in `usbd_process()`. Call it from the USB
interrupt handler (recommended) or from a main loop, but always from one context.
Class callbacks run inside it, so keep them short and do slow work, such as UART
output, in the main loop. Class API functions called from the main loop
(`usbd_cdc_acm_read`/`write`) protect themselves with the port's critical section.

**VBUS and the pull-up.** `usbd_connect(dev, true)` only requests the attach.
Hardware drives the D+ pull-up only while VBUS (`status.vbus_present`) is present,
as USB 2.0 section 7.1.5.1 requires, and every VBUS change raises
`bus_event.vbus`. The HAL reports it as `USB_HAL_EVENT_VBUS`, and `usbd_process()`
follows it: it attaches when VBUS appears; on VBUS loss it releases the pull-up,
closes the configuration and returns to `USBD_STATE_DETACHED` with address 0. The
application does not poll VBUS. A bus reset seen while detached
(floating lines read as SE0) does not change the state.

**Suspend and remote wakeup.** The core sets `status.suspended` after 3 ms of idle
bus; any non-idle signaling (the host's resume, or a bus reset) ends it. The HAL
reports `USB_HAL_EVENT_SUSPEND` and `USB_HAL_EVENT_RESUME`; L3 keeps the device
state, address and configuration across a suspend, exposes `usbd_suspended()` and
calls `usbd_app_t.suspend_changed`. `usbd_remote_wakeup()` wakes the host, only
while suspended and while the host has enabled remote wakeup: hardware waits until
the bus has been idle for 5 ms, drives K for 2 ms, and the host's own resume then
ends the suspend. Reducing power while suspended is up to the application.
`sw/tests/suspend` checks the HAL path and the resume timing on the wire.

**Control transfers.** The status OUT of a control read is armed together with
the data stage, so a host that ends the read early is handled; Windows does this on
its first device-descriptor read. SET_ADDRESS takes effect after its status stage.

**Several classes.** A device registers up to `USBD_MAX_CLASSES` drivers. On
SET_CONFIGURATION, L3 offers each function (an interface association, or an
interface at alternate setting 0) to the registered drivers; a driver claims its
interfaces by returning the bytes of descriptors it consumed and opens its endpoints
with `usbd_ep_open()`. Interface and endpoint requests are routed to the owner;
vendor requests to the device go to `usbd_app_t.vendor_request`. A new class
implements `usbd_class_t` (`reset`, `open`, `control`, `xfer_done`).

**Porting.** Implement the four functions of `usb_port.h` for the target CPU,
pass the core's base address to `usbd_init()`, call `usbd_process()` on its
`irq` (or from the main loop).

### Example: CDC-ACM echo

`sw/app/cdc_echo` is a complete application: a virtual COM port (VID `0x1209`,
PID `0x0001`) that sends back every byte it receives. On hardware, open it with
PuTTY, any terminal or pyserial. Its main loop polls VBUS and never sleeps (no VBUS
interrupt, no timer in this NEORV32 configuration). Its simulation test checks the
pull-up against VBUS (none before plug, dropped on unplug), enumerates the device
the way Windows does, opens the port, checks the echo, and repeats after a replug:

```powershell
.\venv\Scripts\python.exe tools/cocotb_runner.py --sw-test cdc_echo --no-waves
```

`--sw-test` builds `sw/tests/<name>/` or `sw/app/<name>/` (every `.c` file in it).
The library layers have their own tests: `usb_ll_ram` (L1 copies and cycle
budgets) and `usb_hal` (L2 transfers).

## Application

`main.c` boots from IMEM, reads the USB core ID and version through the real
XBUS-to-AXI bridge, and prints both using UART0. The USB peripheral occupies
`0xF0000000` through `0xF0001FFF` in `tb/vhdl/usb_top_tb.vhd`; other external
addresses return a bus error. `include/platform.h` defines the matching software
base address. Core ID is at offset `0x00`, version at `0x04`.

From the repository root, run:

```powershell
.\venv\Scripts\python.exe tools/cocotb_runner.py --firmware-only
```

This automatically runs `make -C sw image`, generates the C header from
`rdl/usb_device.rdl`, builds the firmware, compiles its IMEM image directly into
the simulator's `neorv32` library, and runs the firmware test. Add `--no-waves`
to disable the FST waveform. The simulation runs for 1 ms after reset and displays
the values read by the firmware, without checking register values or matching
expected UART0 text. Full simulator output is saved in
`sim/build/firmware.log`.

Example UART0 output (values depend on the generated register block):

```text
USB core ID: 0x600dc0de
USB version: 0x00010000 (1.0.0)
```

To build the image without simulating:

```powershell
make -C sw image PYTHON=../venv/Scripts/python.exe
```

Alternatively, activate the virtual environment first and use `make -C sw image`
or `make -C sw run-sim`. GNU Make, the `riscv-none-elf-` GCC toolchain, a native
GCC for the image generator, and the existing Python/NVC tools must be on PATH.
The runner passes its own Python interpreter to Make so PeakRDL uses the same
environment. With a different RISC-V toolchain prefix, set `RISCV_PREFIX` when
invoking Make (or in the environment for automatic runner builds).

The Makefile reuses NeoRV32's startup code, libraries, linker script, and image
generator, with an RV32IM ISA (hardware integer multiplication/division) and
memory sizes matching the testbench:
32 KiB IMEM at `0x00000000`, 16 KiB DMEM at `0x80000000`.
The testbench disables both CPU caches (`ICACHE_EN`, `DCACHE_EN`) and enables
the base counters (`RISCV_ISA_Zicntr`, for `mcycle`). `sw/tests/ram_bench` prints
the CPU cycles per access to the USB shared RAM and registers.

Generated headers, objects, the host image-generator executable, and memory
images stay under `sw/` and are ignored by Git. Do not use upstream `install`:
the runner replaces the upstream IMEM image in its source list with
`sw/neorv32_imem_image.vhd`. Upstream `make sim` runs a separate GHDL testbench;
use this project's Python runner or `run-sim` target instead.

`UART0_SIM_MODE` is enabled in this simulation firmware. NeoRV32 sends writes
through UART0's hardware simulation path to the simulator console; no serial
receiver is required. For a hardware build, remove that flag and connect UART0
TX to a pin. This smoke-test application only accesses the two identification
registers; see `sw/app/cdc_echo` for a USB application.
