"""Host side of the CDC-ACM echo application (``sw/app/cdc_echo``).

Run with ``python tools/cocotb_runner.py --sw-test cdc_echo``. The real application
runs on NEORV32 with the whole USB device library, interrupt-driven. The host
enumerates it the way Windows does (first device descriptor read ended early, a
second reset, SET_ADDRESS, descriptors, strings, SET_CONFIGURATION), opens the
virtual COM port (line coding, DTR) and checks that data comes back unchanged.
The pull-up must follow VBUS: released while VBUS is absent after boot, raised on
plug, released on unplug, and a replug enumerates the device again from address 0.
Between them the bus idles into suspend and the host resumes it; the device keeps its
configuration and echoes on.
"""
import struct

import cocotb
from cocotb.triggers import FallingEdge, RisingEdge, Timer, with_timeout

from drivers.usb_fs_bus import CocotbUsbFsPort, UsbFsBus
from drivers.usb_fs_control import UsbFsControl, setup_packet
from drivers.usb_fs_host import UsbFsHost
from models.usb_fs_types import LineState, Pid
from test_usb_top import start_clocks_and_release_reset

ADDRESS = 5
EP_OUT, EP_IN = 2, 2
GET_DESCRIPTOR, SET_ADDRESS, SET_CONFIGURATION, GET_CONFIGURATION, GET_STATUS = 6, 5, 9, 8, 0
DEVICE, CONFIGURATION, STRING, DEVICE_QUALIFIER = 1, 2, 3, 6
SET_LINE_CODING, GET_LINE_CODING, SET_CONTROL_LINE_STATE = 0x20, 0x21, 0x22
RESET_FS = 10_000_000_000
RETRIES = 2000
BOOT_WITHOUT_VBUS_US = 1000
VBUS_RESPONSE_US = 200
SUSPEND_IDLE_US = 3000
SETTLE_US = 100
HOST_RESUME_US = 1000
RESUME_RECOVERY_US = 100
LOW_SPEED_BIT_NS = 667


def get_descriptor(kind: int, index: int, length: int, language: int = 0) -> bytes:
    return setup_packet(0x80, GET_DESCRIPTOR, (kind << 8) | index, language, length)


def string_text(descriptor: bytes) -> str:
    assert descriptor[1] == STRING and descriptor[0] == len(descriptor), f"string descriptor {descriptor.hex()}"
    return descriptor[2:].decode("utf-16-le")


async def bulk_out(host: UsbFsHost, data: bytes, pid: Pid) -> Pid:
    for offset in range(0, len(data), 64):
        for _ in range(RETRIES):
            result = await host.out(data[offset:offset + 64], EP_OUT, pid=pid)
            if result.pid is not Pid.NAK:
                break
            await Timer(2, unit="us")
        assert result.pid is Pid.ACK, f"bulk OUT: {result}"
        pid = Pid.DATA1 if pid is Pid.DATA0 else Pid.DATA0
    return pid


async def bulk_in(host: UsbFsHost, length: int, pid: Pid) -> tuple[bytes, Pid]:
    data = b""
    for _ in range(RETRIES):
        if len(data) >= length:
            break
        result = await host.in_transfer(EP_IN, pid=pid)
        if result.pid is Pid.NAK:
            await Timer(2, unit="us")
            continue
        assert result.success, f"bulk IN expecting {pid.name}: {result}"
        data += result.payload
        pid = Pid.DATA1 if pid is Pid.DATA0 else Pid.DATA0
    return data, pid


@cocotb.test()
async def test_sw_cdc_echo(dut) -> None:
    await start_clocks_and_release_reset(dut, vbus=False)
    host = UsbFsHost(UsbFsBus(CocotbUsbFsPort(dut, dp_drive_name="usb_dp_host", dn_drive_name="usb_dn_host")))
    control = UsbFsControl(host)

    # The application asks to connect right after booting; without VBUS D+ must stay released.
    await Timer(BOOT_WITHOUT_VBUS_US, unit="us")
    assert int(dut.usb_pu.value) == 0, "pull-up raised without VBUS"
    dut.usb_vbus.value = 1
    await with_timeout(RisingEdge(dut.usb_pu), VBUS_RESPONSE_US, "us")
    out_pid, in_pid = await enumerate_and_echo(host, control)

    # Suspend keeps address and configuration (USB 2.0 section 9.1.1.6); after resume the echo works on.
    await Timer(SUSPEND_IDLE_US + SETTLE_US, unit="us")
    assert int(dut.usb_top_inst.usb_suspended.value) == 1, "suspended after 3 ms of idle bus"
    await host_resume(host, HOST_RESUME_US)
    await Timer(RESUME_RECOVERY_US, unit="us")
    assert int(dut.usb_top_inst.usb_suspended.value) == 0, "resumed by the host's resume signaling"
    assert await control.read(setup_packet(0x80, GET_CONFIGURATION, 0, 0, 1)) == b"", "configured after resume"
    await echo(host, (b"after resume",), out_pid, in_pid)

    # Unplug drops the pull-up; replug attaches again and the device enumerates from address 0.
    dut.usb_vbus.value = 0
    await with_timeout(FallingEdge(dut.usb_pu), VBUS_RESPONSE_US, "us")
    dut.usb_vbus.value = 1
    await with_timeout(RisingEdge(dut.usb_pu), VBUS_RESPONSE_US, "us")
    host.address = 0
    await enumerate_and_echo(host, control)


async def enumerate_and_echo(host: UsbFsHost, control: UsbFsControl) -> tuple[Pid, Pid]:
    # Windows: read the device descriptor at address 0, end it after one packet, reset again.
    await host.bus_reset(RESET_FS)
    device = await control.read(get_descriptor(DEVICE, 0, 64), early_status=True)
    assert device is not None and len(device) == 18 and device[7] == 64, f"first device descriptor read: {device}"
    await host.bus_reset(RESET_FS)

    assert await control.write(setup_packet(0x00, SET_ADDRESS, ADDRESS)), "SET_ADDRESS"
    host.address = ADDRESS
    await Timer(50, unit="us")

    device = await control.read(get_descriptor(DEVICE, 0, 18))
    vid, pid_, bcd = struct.unpack_from("<HHH", device, 8)
    assert device[:2] == bytes((18, DEVICE)) and (vid, pid_) == (0x1209, 0x0001), f"device descriptor {device.hex()}"
    assert device[4:7] == bytes((0xEF, 0x02, 0x01)) and device[17] == 1, f"device class / configurations {device.hex()}"

    header = await control.read(get_descriptor(CONFIGURATION, 0, 9))
    total = struct.unpack_from("<H", header, 2)[0]
    assert total == 75 and header[4] == 2, f"configuration header {header.hex()}"
    config = await control.read(get_descriptor(CONFIGURATION, 0, total))
    assert len(config) == total, f"configuration descriptor {config.hex()}"
    interfaces = [config[i:i + 9] for i in range(len(config)) if config[i + 1:i + 2] == b"\x04" and config[i] == 9]
    assert [(itf[2], itf[5], itf[6]) for itf in interfaces] == [(0, 0x02, 0x02), (1, 0x0A, 0x00)], "CDC interfaces"

    languages = await control.read(get_descriptor(STRING, 0, 255))
    assert languages == bytes((4, STRING, 0x09, 0x04)), f"string 0: {languages}"
    texts = [string_text(await control.read(get_descriptor(STRING, i, 255, 0x0409))) for i in (1, 2, 3, 4)]
    assert texts == ["usb-device", "CDC-ACM echo", "0001", "Virtual COM port"], f"strings {texts}"
    assert await control.read(get_descriptor(DEVICE_QUALIFIER, 0, 10)) is None, "full-speed only: STALL"
    assert await control.read(get_descriptor(STRING, 9, 255, 0x0409)) is None, "unknown string: STALL"

    assert await control.write(setup_packet(0x00, SET_CONFIGURATION, 1)), "SET_CONFIGURATION"
    assert await control.read(setup_packet(0x80, GET_CONFIGURATION, 0, 0, 1)) == b"\x01", "GET_CONFIGURATION"
    assert await control.read(setup_packet(0x80, GET_STATUS, 0, 0, 2)) == b"\x00\x00", "bus-powered, no wakeup"

    # Open the port like a terminal: line coding, then DTR and RTS.
    coding = struct.pack("<IBBB", 9600, 0, 0, 8)
    assert await control.write(setup_packet(0x21, SET_LINE_CODING, 0, 0, 7), coding), "SET_LINE_CODING"
    assert await control.read(setup_packet(0xA1, GET_LINE_CODING, 0, 0, 7)) == coding, "GET_LINE_CODING"
    assert await control.write(setup_packet(0x21, SET_CONTROL_LINE_STATE, 0x0003, 0)), "SET_CONTROL_LINE_STATE"

    messages = (b"Hello, USB!", bytes(range(100)), bytes((0x5A ^ i) & 0xFF for i in range(64)))
    return await echo(host, messages, Pid.DATA0, Pid.DATA0)


async def echo(host: UsbFsHost, messages, out_pid: Pid, in_pid: Pid) -> tuple[Pid, Pid]:
    for message in messages:
        out_pid = await bulk_out(host, message, out_pid)
        reply, in_pid = await bulk_in(host, len(message), in_pid)
        assert reply == message, f"echo of {len(message)} bytes: got {reply.hex()}"
    return out_pid, in_pid


async def host_resume(host: UsbFsHost, duration_us: float) -> None:
    """Resume signaling: K, then a low-speed EOP (two low-speed bit times of SE0, then J)."""
    await host.bus.drive(LineState.K)
    await Timer(duration_us, unit="us")
    await host.bus.drive(LineState.SE0)
    await Timer(2 * LOW_SPEED_BIT_NS, unit="ns")
    await host.bus.drive(LineState.J)
    await Timer(LOW_SPEED_BIT_NS, unit="ns")
    await host.bus.release()
