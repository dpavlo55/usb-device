"""Bus timing of the host model: the USB 2.0 values, or a shortened set for regressions.

USBCV sleeps for seconds between steps; simulation cannot afford that. ``SPEC`` uses the
USB 2.0 minimum host-side durations, ``FAST`` shortens the host's waits that the device
cannot observe as a violation. Timers that the *device* must implement (3 ms idle to
suspend, 5 ms before remote wakeup, 1 to 15 ms of remote-wakeup K) are never shortened.
Select with the ``TIMING`` environment variable (``fast``, the default, or ``spec``).
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from .wire import MS_FS, US_FS


@dataclass(frozen=True)
class UsbTiming:
    name: str
    bus_reset_fs: int
    """Host reset signaling (TDRST: at least 10 ms; the device detects it after 2.5 us)."""
    reset_recovery_fs: int
    """Before the first request after reset (TRSTRCY: 10 ms)."""
    attach_debounce_fs: int
    """After the pull-up appears, before the port reset (TATTDB: 100 ms)."""
    set_address_recovery_fs: int
    """After SET_ADDRESS, before using the new address (section 9.2.6.3: 2 ms)."""
    suspend_fs: int
    """Idle bus to suspend the device; above 5 ms so remote wakeup is allowed (TWTRSM)."""
    resume_fs: int
    """Host resume signaling (TDRSMDN: at least 20 ms)."""
    resume_recovery_fs: int
    """After resume, before the first request (TRSMRCY: 10 ms)."""
    remote_wakeup_timeout_fs: int
    """How long the host waits for a device to signal remote wakeup."""
    request_timeout_fs: int
    """NAK retry limit per stage (section 9.2.6.4: 50 ms without data, 500 ms per data packet)."""
    remote_wakeup_resume_fs: int = 20 * MS_FS
    """The host's resume after a remote wakeup (TDRSMDN: 20 ms). Never shortened: the device
    may drive K for up to 15 ms and the host must outlast it."""
    frame_period_fs: int = MS_FS
    response_timeout_bits: float = 18
    """The host gives up on a response after 16 to 18 bit times (section 7.1.19.1)."""
    max_device_response_bits: float = 7.5
    """Device turnaround seen at the host: 6.5 bit times plus cable delay (section 7.1.18.1)."""

    @classmethod
    def from_environment(cls) -> UsbTiming:
        name = os.environ.get("TIMING", "fast").lower()
        presets = {"fast": FAST, "spec": SPEC}
        if name not in presets:
            raise ValueError(f"TIMING must be one of {sorted(presets)}, not {name!r}")
        return presets[name]


SPEC = UsbTiming(
    name="spec",
    bus_reset_fs=10 * MS_FS,
    reset_recovery_fs=10 * MS_FS,
    attach_debounce_fs=100 * MS_FS,
    set_address_recovery_fs=2 * MS_FS,
    suspend_fs=7 * MS_FS,
    resume_fs=20 * MS_FS,
    resume_recovery_fs=10 * MS_FS,
    remote_wakeup_timeout_fs=50 * MS_FS,
    request_timeout_fs=50 * MS_FS,
)

FAST = UsbTiming(
    name="fast",
    bus_reset_fs=20 * US_FS,
    reset_recovery_fs=100 * US_FS,
    attach_debounce_fs=50 * US_FS,
    set_address_recovery_fs=200 * US_FS,
    suspend_fs=7 * MS_FS,
    resume_fs=1 * MS_FS,
    resume_recovery_fs=100 * US_FS,
    remote_wakeup_timeout_fs=20 * MS_FS,
    request_timeout_fs=5 * MS_FS,
)
