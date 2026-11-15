"""Build project firmware and run the NVC/cocotb regression."""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

from cocotb_tools.runner import Nvc, get_results


ROOT = Path(__file__).resolve().parents[1]
TEST_DIR = ROOT / "tb" / "cocotb"
BUILD_DIR = ROOT / "sim" / "build"
FIRMWARE_IMAGE = ROOT / "sw" / "neorv32_imem_image.vhd"
GENERATOR_BUILD_DIR = ROOT / "sim" / "packet_generator_build"
DESERIALIZER_BUILD_DIR = ROOT / "sim" / "packet_deserializer_build"
PARSER_BUILD_DIR = ROOT / "sim" / "packet_parser_build"
SERIALIZER_BUILD_DIR = ROOT / "sim" / "packet_serializer_build"
PHY_TX_BUILD_DIR = ROOT / "sim" / "phy_tx_build"
PHY_TX_SOURCES = [
    ROOT / "rtl" / "usb_pkg.vhd",
    ROOT / "rtl" / "stream_pkg.vhd",
    ROOT / "rtl" / "input_buffer.vhd",
    ROOT / "rtl" / "output_buffer.vhd",
    ROOT / "rtl" / "usb_phy_tx.vhd",
    ROOT / "tb" / "vhdl" / "usb_phy_tx_tb.vhd",
]
SERIALIZER_SOURCES = [
    ROOT / "rtl" / "usb_pkg.vhd",
    ROOT / "rtl" / "stream_pkg.vhd",
    ROOT / "rtl" / "input_buffer.vhd",
    ROOT / "rtl" / "output_buffer.vhd",
    ROOT / "rtl" / "usb_packet_serializer.vhd",
    ROOT / "tb" / "vhdl" / "usb_packet_serializer_tb.vhd",
]
DESERIALIZER_SOURCES = [
    ROOT / "rtl" / "usb_pkg.vhd",
    ROOT / "rtl" / "stream_pkg.vhd",
    ROOT / "rtl" / "edge_detector.vhd",
    ROOT / "rtl" / "output_buffer.vhd",
    ROOT / "rtl" / "usb_phy_rx.vhd",
    ROOT / "rtl" / "usb_packet_deserializer.vhd",
    ROOT / "tb" / "vhdl" / "usb_packet_deserializer_tb.vhd",
]
GENERATOR_SOURCES = [
    ROOT / "rtl" / "usb_pkg.vhd",
    ROOT / "rtl" / "stream_pkg.vhd",
    ROOT / "rtl" / "input_buffer.vhd",
    ROOT / "rtl" / "output_buffer.vhd",
    ROOT / "rtl" / "elastic_fifo.vhd",
    ROOT / "rtl" / "crc.vhd",
    ROOT / "rtl" / "usb_packet_generator.vhd",
    ROOT / "tb" / "vhdl" / "usb_packet_generator_tb.vhd",
]
RAM_USB_ADAPTER_BUILD_DIR = ROOT / "sim" / "ram_usb_adapter_build"
RAM_USB_ADAPTER_SOURCES = [
    ROOT / "rtl" / "usb_pkg.vhd",
    ROOT / "rtl" / "stream_pkg.vhd",
    ROOT / "rtl" / "input_buffer.vhd",
    ROOT / "rtl" / "output_buffer.vhd",
    ROOT / "rtl" / "dual_port_ram.vhd",
    ROOT / "rtl" / "usb_ram.vhd",
    ROOT / "rtl" / "usb_ram_usb_adapter.vhd",
    ROOT / "tb" / "vhdl" / "usb_ram_usb_adapter_tb.vhd",
]
CDC_BUILD_DIR = ROOT / "sim" / "cdc_build"
CDC_SOURCES = [
    ROOT / "rtl" / "cdc_sync_single.vhd",
    ROOT / "rtl" / "edge_detector.vhd",
    ROOT / "rtl" / "cdc_pulse.vhd",
    ROOT / "rtl" / "cdc_handshake.vhd",
    ROOT / "tb" / "vhdl" / "cdc_tb.vhd",
]
ENDPOINTS_BUILD_DIR = ROOT / "sim" / "endpoints_build"
ENDPOINTS_SOURCES = [
    ROOT / "rtl" / "usb_pkg.vhd",
    ROOT / "rtl" / "stream_pkg.vhd",
    ROOT / "rtl" / "cdc_sync_single.vhd",
    ROOT / "rtl" / "edge_detector.vhd",
    ROOT / "rtl" / "cdc_pulse.vhd",
    ROOT / "rtl" / "cdc_handshake.vhd",
    ROOT / "rtl" / "usb_cdc.vhd",
    ROOT / "rtl" / "input_buffer.vhd",
    ROOT / "rtl" / "output_buffer.vhd",
    ROOT / "rtl" / "dual_port_ram.vhd",
    ROOT / "rtl" / "usb_ram.vhd",
    ROOT / "rtl" / "usb_ram_sw_adapter.vhd",
    ROOT / "rtl" / "usb_ram_usb_adapter.vhd",
    ROOT / "rtl" / "usb_rx_filter.vhd",
    ROOT / "rtl" / "usb_setup_stage.vhd",
    ROOT / "rtl" / "usb_endpoints_controller.vhd",
    ROOT / "rtl" / "usb_endpoints.vhd",
    ROOT / "tb" / "vhdl" / "usb_endpoints_tb.vhd",
]
PARSER_SOURCES = [
    ROOT / "rtl" / "usb_pkg.vhd",
    ROOT / "rtl" / "stream_pkg.vhd",
    ROOT / "rtl" / "crc.vhd",
    ROOT / "rtl" / "input_buffer.vhd",
    ROOT / "rtl" / "output_buffer.vhd",
    ROOT / "rtl" / "single_buffer.vhd",
    ROOT / "rtl" / "elastic_buffer.vhd",
    ROOT / "rtl" / "elastic_fifo.vhd",
    ROOT / "rtl" / "usb_packet_parser.vhd",
    ROOT / "tb" / "vhdl" / "usb_packet_parser_tb.vhd",
]
PROTOCOL_BUILD_DIR = ROOT / "sim" / "protocol_build"
PROTOCOL_SOURCES = [
    ROOT / "rtl" / "usb_pkg.vhd",
    ROOT / "rtl" / "usb_protocol.vhd",
    ROOT / "tb" / "vhdl" / "usb_protocol_tb.vhd",
]
RX_FILTER_BUILD_DIR = ROOT / "sim" / "rx_filter_build"
RX_FILTER_SOURCES = [
    ROOT / "rtl" / "usb_pkg.vhd",
    ROOT / "rtl" / "stream_pkg.vhd",
    ROOT / "rtl" / "input_buffer.vhd",
    ROOT / "rtl" / "output_buffer.vhd",
    ROOT / "rtl" / "usb_rx_filter.vhd",
    ROOT / "tb" / "vhdl" / "usb_rx_filter_tb.vhd",
]
SETUP_STAGE_BUILD_DIR = ROOT / "sim" / "setup_stage_build"
SETUP_STAGE_SOURCES = [
    ROOT / "rtl" / "usb_pkg.vhd",
    ROOT / "rtl" / "stream_pkg.vhd",
    ROOT / "rtl" / "input_buffer.vhd",
    ROOT / "rtl" / "output_buffer.vhd",
    ROOT / "rtl" / "usb_setup_stage.vhd",
    ROOT / "tb" / "vhdl" / "usb_setup_stage_tb.vhd",
]
BUS_STATE_BUILD_DIR = ROOT / "sim" / "bus_state_build"
BUS_STATE_SOURCES = [
    ROOT / "rtl" / "usb_pkg.vhd",
    ROOT / "rtl" / "usb_bus_state.vhd",
    ROOT / "tb" / "vhdl" / "usb_bus_state_tb.vhd",
]
TURNAROUND_BUILD_DIR = ROOT / "sim" / "turnaround_build"
TURNAROUND_SOURCES = [
    ROOT / "rtl" / "usb_pkg.vhd",
    ROOT / "rtl" / "stream_pkg.vhd",
    ROOT / "rtl" / "cdc_sync_single.vhd",
    ROOT / "rtl" / "edge_detector.vhd",
    ROOT / "rtl" / "crc.vhd",
    ROOT / "rtl" / "input_buffer.vhd",
    ROOT / "rtl" / "output_buffer.vhd",
    ROOT / "rtl" / "single_buffer.vhd",
    ROOT / "rtl" / "elastic_buffer.vhd",
    ROOT / "rtl" / "elastic_fifo.vhd",
    ROOT / "rtl" / "usb_phy_rx.vhd",
    ROOT / "rtl" / "usb_packet_deserializer.vhd",
    ROOT / "rtl" / "usb_packet_parser.vhd",
    ROOT / "rtl" / "usb_packet_generator.vhd",
    ROOT / "rtl" / "usb_packet_serializer.vhd",
    ROOT / "rtl" / "usb_phy_tx.vhd",
    ROOT / "tb" / "vhdl" / "usb_turnaround_tb.vhd",
]
SOURCES = [
    ROOT / "rtl" / "usb_pkg.vhd",
    ROOT / "rtl" / "stream_pkg.vhd",
    ROOT / "rtl" / "cdc_sync_single.vhd",
    ROOT / "rtl" / "edge_detector.vhd",
    ROOT / "rtl" / "cdc_pulse.vhd",
    ROOT / "rtl" / "cdc_handshake.vhd",
    ROOT / "rtl" / "usb_cdc.vhd",
    ROOT / "rtl" / "crc.vhd",
    ROOT / "rtl" / "input_buffer.vhd",
    ROOT / "rtl" / "output_buffer.vhd",
    ROOT / "rtl" / "single_buffer.vhd",
    ROOT / "rtl" / "elastic_buffer.vhd",
    ROOT / "rtl" / "elastic_fifo.vhd",
    ROOT / "rtl" / "usb_phy_rx.vhd",
    ROOT / "rtl" / "usb_packet_deserializer.vhd",
    ROOT / "rtl" / "usb_packet_serializer.vhd",
    ROOT / "rtl" / "usb_phy_tx.vhd",
    ROOT / "rtl" / "usb_packet_parser.vhd",
    ROOT / "rtl" / "usb_packet_generator.vhd",
    ROOT / "rtl" / "usb_device_regs_pkg.vhd",
    ROOT / "rtl" / "reg_utils.vhd",
    ROOT / "rtl" / "usb_device_regs.vhd",
    ROOT / "rtl" / "dual_port_ram.vhd",
    ROOT / "rtl" / "usb_ram.vhd",
    ROOT / "rtl" / "usb_ram_sw_adapter.vhd",
    ROOT / "rtl" / "usb_ram_usb_adapter.vhd",
    ROOT / "rtl" / "usb_rx_filter.vhd",
    ROOT / "rtl" / "usb_setup_stage.vhd",
    ROOT / "rtl" / "usb_endpoints_controller.vhd",
    ROOT / "rtl" / "usb_endpoints.vhd",
    ROOT / "rtl" / "usb_protocol.vhd",
    ROOT / "rtl" / "usb_bus_state.vhd",
    ROOT / "rtl" / "usb_top.vhd",
    ROOT / "tb" / "vhdl" / "usb_top_tb.vhd",
]


def neorv32_sources(firmware_image: Path = FIRMWARE_IMAGE) -> list[Path]:
    """Expand NEORV32's simulator-independent core file list."""
    neorv32_root = ROOT / "neorv32"
    file_list = neorv32_root / "rtl" / "file_list_core.f"
    sources = [
        firmware_image if line.strip().endswith("/neorv32_imem_image.vhd") else
        Path(line.strip().replace("$NEORV32_HOME", str(neorv32_root)))
        for line in file_list.read_text().splitlines()
        if line.strip()
    ]
    sources.append(neorv32_root / "rtl" / "system_integration" / "xbus2axi4_bridge.vhd")
    return sources


ALL_TEST_MODULES = [
    "test_usb_firmware",
    "test_usb_top",
    "test_usb_phy_rx_random",
    "test_usb_phy_rx_packets_random",
    "test_usb_phy_rx_errors",
    "test_usb_token_packets",
    "test_usb_data_packets",
    "test_usb_handshake_packets",
    "test_usb_special_packets",
    "test_usb_mixed_packets",
]
PACKET_TEST_MODULES = [
    "test_usb_token_packets",
    "test_usb_data_packets",
    "test_usb_handshake_packets",
    "test_usb_special_packets",
    "test_usb_mixed_packets",
]


class RelaxedNvc(Nvc):
    """NVC runner that analyzes with --relaxed.

    dual_port_ram.vhd uses a plain shared variable (the synthesis template for
    a RAM with two write ports). VHDL-2008 requires a protected type there, and
    --relaxed turns that error into a warning. nvc only accepts the option
    after -a, so it cannot be passed through build_args.
    """

    def _build_command(self):
        cmds = super()._build_command()
        for cmd in cmds:
            cmd.insert(cmd.index("-a") + 1, "--relaxed")
        return cmds


def sw_program_dir(name: str) -> Path | None:
    """A firmware test (sw/tests/<name>) or an application (sw/app/<name>) with a main.c."""
    for directory in (ROOT / "sw" / "tests" / name, ROOT / "sw" / "app" / name):
        if (directory / "main.c").is_file():
            return directory
    return None


def sw_program_sources(name: str) -> list[str]:
    """The program's C files relative to sw/, main.c first (the Makefile builds it specially)."""
    directory = sw_program_dir(name)
    relative = directory.relative_to(ROOT / "sw").as_posix()
    others = sorted(p.name for p in directory.glob("*.c") if p.name != "main.c")
    return [f"{relative}/main.c", *(f"{relative}/{name}" for name in others)]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--test-filter", help="Regular expression selecting cocotb tests")
    parser.add_argument("--sw-test", help="Build sw/tests/<name>/ or sw/app/<name>/ (all its .c files) for a top-level test")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--firmware-only",
        action="store_true",
        help="Boot sw/main.c and display register values through UART0 simulation output",
    )
    mode.add_argument(
        "--packet-only",
        action="store_true",
        help="Run only the token, handshake, special, mixed, and DATA packet suites",
    )
    mode.add_argument(
        "--generator-only",
        action="store_true",
        help="Run the packet generator test using its standalone harness",
    )
    mode.add_argument(
        "--phy-tx-only",
        action="store_true",
        help="Check serial stream bit stuffing, NRZI, and four-clock USB TX pin timing",
    )
    mode.add_argument(
        "--serializer-only",
        action="store_true",
        help="Check byte-to-bit AXI4-Stream serialization with and without backpressure",
    )
    mode.add_argument(
        "--deserializer-only",
        action="store_true",
        help="Check the deserializer AXI4-Stream interface in its standalone harness",
    )
    mode.add_argument(
        "--parser-only",
        action="store_true",
        help="Check the parser AXI4-Stream payload interface in a standalone harness",
    )
    mode.add_argument(
        "--ram-usb-adapter-only",
        action="store_true",
        help="Check usb_ram_usb_adapter (USB-side shared RAM adapter) in a standalone harness",
    )
    mode.add_argument(
        "--cdc-only",
        action="store_true",
        help="Check the cdc_pulse and cdc_handshake primitives at several clock ratios",
    )
    mode.add_argument(
        "--endpoints-only",
        action="store_true",
        help="Check usb_cdc + usb_endpoints (ownership, transfer events) in a standalone harness",
    )
    mode.add_argument(
        "--protocol-only",
        action="store_true",
        help="Check usb_protocol (token to endpoint target) in a standalone harness",
    )
    mode.add_argument(
        "--rx-filter-only",
        action="store_true",
        help="Check usb_rx_filter (route, drop, buffer-size limit) in a standalone harness",
    )
    mode.add_argument(
        "--setup-stage-only",
        action="store_true",
        help="Check usb_setup_stage (SETUP data written after acceptance) in a standalone harness",
    )
    mode.add_argument(
        "--bus-state-only",
        action="store_true",
        help="Check usb_bus_state (suspend, resume, remote wakeup, SOF) in a standalone harness",
    )
    mode.add_argument(
        "--turnaround-only",
        action="store_true",
        help="Measure host EOP to device SYNC turnaround through the RX and TX chains",
    )
    parser.add_argument(
        "--waves",
        dest="waves",
        action="store_true",
        default=True,
        help="Request simulator waveform output (the default)",
    )
    parser.add_argument(
        "--no-waves",
        dest="waves",
        action="store_false",
        help="Disable simulator waveform output",
    )
    parser.add_argument("--verbose", action="store_true", help="Show simulator commands")
    args = parser.parse_args()
    if args.sw_test:
        if any((args.generator_only, args.phy_tx_only, args.serializer_only,
                args.deserializer_only, args.parser_only,
                args.ram_usb_adapter_only, args.cdc_only, args.endpoints_only,
                args.turnaround_only, args.protocol_only, args.bus_state_only,
                args.rx_filter_only, args.setup_stage_only)):
            parser.error("--sw-test requires a top-level test, not a standalone harness")
        if not re.fullmatch(r"[A-Za-z0-9_]+", args.sw_test):
            parser.error("--sw-test must contain only letters, digits, and underscores")
        if sw_program_dir(args.sw_test) is None:
            parser.error(f"unknown software test or application: {args.sw_test}")

    # cocotb's runner derives the child PYTHONPATH from this process's sys.path.
    sys.path.insert(0, str(TEST_DIR))

    if args.phy_tx_only:
        top = "usb_phy_tx_tb"
        sources = PHY_TX_SOURCES
        build_dir = PHY_TX_BUILD_DIR
        test_modules = ["test_usb_phy_tx"]
        if not (ROOT / "rtl" / "usb_phy_tx.vhd").is_file():
            parser.error("--phy-tx-only requires rtl/usb_phy_tx.vhd; the TX RTL has not been added yet")
    elif args.serializer_only:
        top = "usb_packet_serializer_tb"
        sources = SERIALIZER_SOURCES
        build_dir = SERIALIZER_BUILD_DIR
        test_modules = ["test_usb_packet_serializer"]
    elif args.parser_only:
        top = "usb_packet_parser_tb"
        sources = PARSER_SOURCES
        build_dir = PARSER_BUILD_DIR
        test_modules = ["test_usb_packet_parser"]
    elif args.deserializer_only:
        top = "usb_packet_deserializer_tb"
        sources = DESERIALIZER_SOURCES
        build_dir = DESERIALIZER_BUILD_DIR
        test_modules = ["test_usb_packet_deserializer"]
    elif args.ram_usb_adapter_only:
        top = "usb_ram_usb_adapter_tb"
        sources = RAM_USB_ADAPTER_SOURCES
        build_dir = RAM_USB_ADAPTER_BUILD_DIR
        test_modules = ["test_usb_ram_usb_adapter"]
    elif args.cdc_only:
        top = "cdc_tb"
        sources = CDC_SOURCES
        build_dir = CDC_BUILD_DIR
        test_modules = ["test_cdc"]
    elif args.endpoints_only:
        top = "usb_endpoints_tb"
        sources = ENDPOINTS_SOURCES
        build_dir = ENDPOINTS_BUILD_DIR
        test_modules = ["test_usb_endpoints"]
    elif args.protocol_only:
        top = "usb_protocol_tb"
        sources = PROTOCOL_SOURCES
        build_dir = PROTOCOL_BUILD_DIR
        test_modules = ["test_usb_protocol"]
    elif args.rx_filter_only:
        top = "usb_rx_filter_tb"
        sources = RX_FILTER_SOURCES
        build_dir = RX_FILTER_BUILD_DIR
        test_modules = ["test_usb_rx_filter"]
    elif args.setup_stage_only:
        top = "usb_setup_stage_tb"
        sources = SETUP_STAGE_SOURCES
        build_dir = SETUP_STAGE_BUILD_DIR
        test_modules = ["test_usb_setup_stage"]
    elif args.bus_state_only:
        top = "usb_bus_state_tb"
        sources = BUS_STATE_SOURCES
        build_dir = BUS_STATE_BUILD_DIR
        test_modules = ["test_usb_bus_state"]
    elif args.turnaround_only:
        top = "usb_turnaround_tb"
        sources = TURNAROUND_SOURCES
        build_dir = TURNAROUND_BUILD_DIR
        test_modules = ["test_usb_turnaround"]
    elif args.generator_only:
        top = "usb_packet_generator_tb"
        sources = GENERATOR_SOURCES
        build_dir = GENERATOR_BUILD_DIR
        test_modules = ["test_usb_packet_generator"]
    else:
        top = "usb_top_tb"
        sources = SOURCES
        build_dir = ROOT / "sim" / "firmware_tests" / args.sw_test if args.sw_test else BUILD_DIR
        rdl_inputs = [ROOT / "rdl" / "usb_device.rdl", ROOT / "rdl" / "Makefile"]
        generated_regs = [ROOT / "rtl" / "usb_device_regs.vhd", ROOT / "rtl" / "usb_device_regs_pkg.vhd"]
        if any(not path.is_file() for path in generated_regs) or \
                max(p.stat().st_mtime for p in rdl_inputs) > min(p.stat().st_mtime for p in generated_regs):
            subprocess.run(["make", "-C", str(ROOT / "rdl"), "regs", f"PYTHON={Path(sys.executable).as_posix()}"],
                           check=True)
        # A firmware test may come with its own host-side module, test_sw_<name>.py.
        sw_module = f"test_sw_{args.sw_test}" if args.sw_test else None
        sw_modules = [sw_module] if sw_module and (TEST_DIR / f"{sw_module}.py").is_file() else ["test_usb_sw"]
        test_modules = (PACKET_TEST_MODULES if args.packet_only else
                        [*sw_modules, *ALL_TEST_MODULES] if args.sw_test and args.test_filter and not args.firmware_only else
                        sw_modules if args.sw_test else
                        ["test_usb_firmware"] if args.firmware_only else
                        ALL_TEST_MODULES)

    runner = RelaxedNvc()
    if top == "usb_top_tb":
        # Rebuild before compiling VHDL so main.c/RDL changes cannot leave a
        # stale boot image. Do not install anything into the NEORV32 submodule.
        make_args = ["make", "-C", str(ROOT / "sw"), "image",
                     f"PYTHON={Path(sys.executable).as_posix()}"]
        firmware_image = FIRMWARE_IMAGE
        if args.sw_test:
            output_dir = f"build/tests/{args.sw_test}"
            firmware_image = ROOT / "sw" / output_dir / "neorv32_imem_image.vhd"
            make_args += [f"SW_TEST={args.sw_test}", f"APP_SRC={' '.join(sw_program_sources(args.sw_test))}",
                          f"BUILD_DIR={output_dir}",
                          f"APP_ELF={output_dir}/main.elf",
                          f"APP_FLT={output_dir}/elf.bin",
                          f"APP_VHD={output_dir}/neorv32_imem_image.vhd"]
        subprocess.run(make_args, check=True)
        # NEORV32 uses its own VHDL library. Compile its published file list
        # first, then compile this project's top-level design against it.
        runner.build(
            sources=neorv32_sources(firmware_image),
            hdl_library="neorv32",
            hdl_toplevel="neorv32_top",
            build_args=["--std=08", "--ieee-warnings=off"],
            build_dir=build_dir,
            clean=True,
            verbose=args.verbose,
        )
        runner.build(
            sources=sources,
            hdl_library="top",
            hdl_toplevel=top,
            build_args=["--std=08", "--ieee-warnings=off"],
            build_dir=build_dir,
            clean=False,
            verbose=args.verbose,
        )
    else:
        runner.build(
            sources=sources,
            hdl_toplevel=top,
            build_args=["--std=08", "--ieee-warnings=off"],
            build_dir=build_dir,
            clean=True,
            verbose=args.verbose,
        )
    try:
        results_file = runner.test(
            test_module=test_modules,
            hdl_toplevel=top,
            hdl_toplevel_lang="vhdl",
            build_dir=build_dir,
            test_dir=build_dir,
            test_filter=args.test_filter,
            extra_env={"USB_SW_TEST": "1" if args.sw_test else "0"},
            # Keep the DUT's record port visible instead of only its parent signal.
            elab_args=["--no-collapse"] if args.generator_only and args.waves else [],
            test_args=["--dump-arrays"],
            waves=args.waves,
            verbose=args.verbose,
            log_file=build_dir / "firmware.log" if args.firmware_only else None,
        )
    finally:
        if args.sw_test:
            waveform = build_dir / "usb_top_tb.fst"
            if waveform.is_file():
                BUILD_DIR.mkdir(parents=True, exist_ok=True)
                shared_waveform = BUILD_DIR / "usb_top_tb.fst"
                shutil.copyfile(waveform, shared_waveform)
                print(f"Waveform: {shared_waveform}")
        firmware_log = build_dir / "firmware.log"
        if args.firmware_only and firmware_log.is_file():
            console = firmware_log.read_text()
            # Firmware and simulator output share the same stream, so retain
            # every line to avoid hiding messages with arbitrary contents.
            print(f"Firmware simulation log: {firmware_log}")
            print(console, end="" if console.endswith("\n") else "\n")
    tests_run, tests_failed = get_results(results_file)
    if tests_run == 0 or tests_failed:
        raise SystemExit(f"cocotb regression failed: {tests_failed} failed of {tests_run} test(s)")


if __name__ == "__main__":
    main()
