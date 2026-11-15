from tb.cocotb.models.usb_fs_timing import (
    FSTimingScheduler,
    FS_CELL_PERIODS_FS,
    FS_RESET_DEFAULT_FS,
    FS_RESET_DETECT_FS,
    reset_duration_fs,
)


def test_exact_three_cell_schedule() -> None:
    schedule = FSTimingScheduler()
    assert tuple(schedule.periods(3)) == FS_CELL_PERIODS_FS
    assert sum(schedule.periods(3)) == 250_000_000
    assert schedule.time_fs(3) == 250_000_000


def test_reset_helper_uses_integer_femtoseconds() -> None:
    assert reset_duration_fs() == FS_RESET_DEFAULT_FS
    assert reset_duration_fs(FS_RESET_DETECT_FS) == FS_RESET_DETECT_FS
    assert FSTimingScheduler().advance(3).time_fs() == 250_000_000