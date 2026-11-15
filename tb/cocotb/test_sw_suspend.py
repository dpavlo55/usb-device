"""Host side of the ``suspend`` firmware test (``sw/tests/suspend``).

Run with ``python tools/cocotb_runner.py --sw-test suspend``. After a bus reset the
host leaves the bus idle. The firmware gets the suspend event and asks for remote
wakeup; the host checks the device's resume signaling against USB 2.0 section
7.1.7.7: not before the bus has been idle for 5 ms (TWTRSM), K held 1 to 15 ms
(TDRSMUP; the core drives 2 ms), then the bus released. Like an upstream hub, the
host joins with its own K within 1 ms (TURSM) and ends it with a low-speed EOP; the
firmware must then see resume. A second idle period and a host-initiated resume
follow. The firmware reports through ``sw_test_status``.
"""
import cocotb
from cocotb.triggers import FallingEdge, RisingEdge, Timer, with_timeout
from cocotb.utils import get_sim_time

from drivers.usb_fs_bus import CocotbUsbFsPort, UsbFsBus
from drivers.usb_fs_host import UsbFsHost
from models.usb_fs_types import LineState
from test_usb_sw import wait_for_sw_result
from test_usb_top import start_clocks_and_release_reset

BUS_RESET_FS = 10_000_000_000
LOW_SPEED_BIT_NS = 667
HOST_JOIN_US = 500
HOST_RESUME_US = 3000
"""Shortened from 20 ms (TDRSMDN) to keep the simulation short; it outlasts the device's K."""
SUSPEND_IDLE_US = 3000
TWTRSM_US = 5000
TDRSMUP_MIN_US, TDRSMUP_MAX_US = 1000, 15000
SETTLE_US = 100


def now_us() -> float:
    return get_sim_time(unit="ns") / 1000


async def host_resume(host: UsbFsHost, duration_us: float) -> None:
    await host.bus.drive(LineState.K)
    await Timer(duration_us, unit="us")
    await host.bus.drive(LineState.SE0)
    await Timer(2 * LOW_SPEED_BIT_NS, unit="ns")
    await host.bus.drive(LineState.J)
    await Timer(LOW_SPEED_BIT_NS, unit="ns")
    await host.bus.release()


def bus_is_k(dut) -> bool:
    return str(dut.usb_dp.value) == "0" and str(dut.usb_dn.value) == "1"


@cocotb.test()
async def test_sw_suspend(dut) -> None:
    sw_result = cocotb.start_soon(wait_for_sw_result(dut, timeout_ms=40))
    await with_timeout(start_clocks_and_release_reset(dut), 5, "ms")
    host = UsbFsHost(UsbFsBus(CocotbUsbFsPort(dut, dp_drive_name="usb_dp_host", dn_drive_name="usb_dn_host")))
    drive = dut.usb_top_inst.resume_drive

    await host.bus.drive(LineState.SE0)
    await Timer(BUS_RESET_FS, unit="fs")
    await host.bus.release()
    idle_start = now_us()

    # Remote wakeup: the firmware requests it on the suspend event, hardware waits for TWTRSM.
    await with_timeout(RisingEdge(drive), 3 * TWTRSM_US, "us")
    k_start = now_us()
    await Timer(1, unit="ns")
    assert bus_is_k(dut), "the device drives K on D+/D- during remote wakeup"
    idle = k_start - idle_start
    assert TWTRSM_US <= idle <= TWTRSM_US + 50, f"remote wakeup K after {idle:.1f} us of idle bus (TWTRSM 5 ms)"

    await Timer(HOST_JOIN_US, unit="us")
    joined = cocotb.start_soon(host_resume(host, HOST_RESUME_US))
    await with_timeout(FallingEdge(drive), TDRSMUP_MAX_US, "us")
    k_length = now_us() - k_start
    assert TDRSMUP_MIN_US <= k_length <= TDRSMUP_MAX_US, f"device K held {k_length:.1f} us (TDRSMUP 1 to 15 ms)"
    assert abs(k_length - 2000) < 1, f"the core drives K for 2 ms, measured {k_length:.1f} us"
    await joined

    # Second suspend: the host resumes on its own after the device has suspended.
    await Timer(SUSPEND_IDLE_US + SETTLE_US, unit="us")
    assert int(dut.usb_top_inst.usb_suspended.value) == 1, "suspended again after 3 ms of idle bus"
    assert int(drive.value) == 0, "no remote wakeup without a request"
    await host_resume(host, 1000)
    await sw_result
