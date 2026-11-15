"""Throughput benchmark for the USB device running sw/app/cdc_bench, over its CDC-ACM port.

    python tools/usb_bench.py                        # all modes, 1 MiB, 3 repeats
    python tools/usb_bench.py --mode source --size 4M --repeat 5
    python tools/usb_bench.py --json base.json       # save results
    python tools/usb_bench.py --compare base.json    # show the change against saved results

The device must already run sw/app/cdc_bench; the script talks to it only through
the virtual COM port, so it does not depend on the board or the CPU.

Modes: sink (host -> device), source (device -> host), echo (both ways, loopback),
sink_check (sink with the device comparing every byte). Every run checks the data:
the host compares what it receives with the pattern, the device counts mismatches in
sink_check. "host" rates are timed on the PC from the first to the last byte, "device"
rates by the device's cycle counter over the same span. The full-speed bulk ceiling is
19 x 64 bytes per 1 ms frame = 1.216 MB/s per direction (USB 2.0 5.8.4).

Progress bars are drawn from the main thread at most 5 times a second; the data moves
in worker threads, so they do not slow the transfer. --no-progress turns them off.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import serial
from serial.tools import list_ports

USB_VID, USB_PID = 0x1209, 0x0001
CEILING = 19 * 64 * 1000  # bytes/s per direction
CHUNK = 16 * 1024
PATTERN = bytes((i + (i >> 8)) & 0xFF for i in range(65536))
MODES = ("sink", "sink_check", "source", "echo")


def pattern(position: int, length: int) -> bytes:
    out = bytearray()
    while length > 0:
        offset = position & 0xFFFF
        n = min(length, 65536 - offset)
        out += PATTERN[offset:offset + n]
        position += n
        length -= n
    return bytes(out)


def parse_size(text: str) -> int:
    text = text.strip().upper()
    scale = {"K": 1024, "M": 1024 * 1024}.get(text[-1:], 1)
    return int(float(text[:-1] if scale > 1 else text) * scale)


def human(rate: float) -> str:
    return f"{rate / 1e6:6.3f} MB/s"


@dataclass
class Result:
    mode: str
    size: int
    host_rate: float
    device_rate: float
    irqs: int
    errors: int
    ok: bool
    isr_avg: int = 0
    isr_max: int = 0
    gaps: tuple[int, ...] = ()
    profile: tuple[int, ...] = ()


class Progress:
    def __init__(self, label: str, total: int, enabled: bool) -> None:
        self.label, self.total, self.enabled = label, total, enabled
        self.start = time.perf_counter()
        self.last = 0.0

    def show(self, done: int, final: bool = False) -> None:
        now = time.perf_counter()
        if not self.enabled or (not final and now - self.last < 0.2):
            return
        self.last = now
        elapsed = max(now - self.start, 1e-9)
        frac = done / self.total if self.total else 1.0
        bar = "#" * int(30 * frac) + "." * (30 - int(30 * frac))
        sys.stdout.write(f"\r  {self.label:<11}[{bar}] {100 * frac:5.1f} %  {done / elapsed / 1e6:6.3f} MB/s ")
        if final:
            sys.stdout.write("\n")
        sys.stdout.flush()


class Bench:
    def __init__(self, port: str, progress: bool) -> None:
        self.serial = serial.Serial(port, timeout=0.05, write_timeout=10)
        self.progress = progress
        self.serial.reset_input_buffer()
        self.clock, self.rx_ring, self.tx_ring, self.chunk = self.info()

    def line(self, timeout: float = 10.0) -> str:
        end = time.monotonic() + timeout
        data = bytearray()
        while time.monotonic() < end:
            data += self.serial.read(1)
            if data.endswith(b"\n"):
                return data.decode(errors="replace").strip()
        raise TimeoutError(f"no reply from the device (got {bytes(data)!r})")

    def info(self) -> tuple[int, int, int, int]:
        self.serial.write(b"\ninfo\n")
        while True:
            text = self.line()
            if text.startswith("INFO"):
                return tuple(int(v) for v in text.split()[1:5])  # type: ignore[return-value]

    def micro_benchmarks(self) -> list[int]:
        self.serial.write(b"ubench\n")
        reply = self.line().split()
        return [int(v) for v in reply[1:]] if reply and reply[0] == "UBENCH" else []

    def run(self, mode: str, size: int) -> Result:
        command = {"sink": f"sink {size} 0", "sink_check": f"sink {size} 1",
                   "source": f"source {size}", "echo": f"echo {size}"}[mode]
        self.serial.write(command.encode() + b"\n")
        sent = [0]
        received = [0]
        first = [0.0]
        last = [0.0]
        mismatch = [False]
        writes = mode in ("sink", "sink_check", "echo")
        reads = mode in ("source", "echo")

        def writer() -> None:
            position = 0
            first[0] = first[0] or time.perf_counter()
            while position < size:
                n = min(CHUNK, size - position)
                self.serial.write(pattern(position, n))
                position += n
                sent[0] = position
            if not reads:
                last[0] = time.perf_counter()

        def reader() -> None:
            position = 0
            deadline = time.monotonic() + 30 + size / 100e3
            while position < size and time.monotonic() < deadline:
                data = self.serial.read(min(CHUNK, size - position))
                if not data:
                    continue
                if position == 0 and not writes:
                    first[0] = time.perf_counter()
                if data != pattern(position, len(data)):
                    mismatch[0] = True
                position += len(data)
                received[0] = position
            last[0] = time.perf_counter()

        threads = []
        if writes:
            threads.append(threading.Thread(target=writer, daemon=True))
        if reads:
            threads.append(threading.Thread(target=reader, daemon=True))
        bar = Progress(mode, size, self.progress)
        for t in threads:
            t.start()
        while any(t.is_alive() for t in threads):
            bar.show(received[0] if reads else sent[0])
            time.sleep(0.05)
        bar.show(received[0] if reads else sent[0], final=True)

        reply = self.line(timeout=30 + size / 100e3).split()
        if reads and received[0] != size:
            raise RuntimeError(f"{mode}: received {received[0]} of {size} bytes")
        if len(reply) != 6 or reply[0] != "OK" or reply[2] != str(size):
            raise RuntimeError(f"{mode}: unexpected reply {' '.join(reply)}")
        cycles, irqs, errors = int(reply[3]), int(reply[4]), int(reply[5])
        host_rate = size / max(last[0] - first[0], 1e-9)
        device_rate = size * self.clock / cycles if cycles else 0.0
        self.serial.write(b"stats\n")
        stats = self.line().split()
        isr_avg, isr_max, gaps = (0, 0, ())
        if stats and stats[0] == "STATS":
            isr_avg, isr_max, gaps = int(stats[1]), int(stats[2]), tuple(int(v) for v in stats[3:11])
        self.serial.write(b"profile\n")
        profile_reply = self.line().split()
        profile = tuple(int(v) for v in profile_reply[1:]) if profile_reply[:1] == ["PROFILE"] else ()
        return Result(mode, size, host_rate, device_rate, irqs, errors, not mismatch[0] and errors == 0,
                      isr_avg, isr_max, gaps, profile)


def find_port() -> str:
    for _ in range(40):
        ports = [p.device for p in list_ports.comports() if p.vid == USB_VID and p.pid == USB_PID]
        if ports:
            return ports[0]
        time.sleep(0.5)
    sys.exit("the USB device (VID 1209, PID 0001) is not connected")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", help="the device's COM port (found by VID:PID by default)")
    parser.add_argument("--mode", choices=(*MODES, "all"), default="all")
    parser.add_argument("--size", default="1M", help="bytes per run, e.g. 256K, 4M (default 1M)")
    parser.add_argument("--repeat", type=int, default=3, help="runs per mode; the median is reported")
    parser.add_argument("--json", type=Path, help="save the results to this file")
    parser.add_argument("--compare", type=Path, help="compare with results saved by --json")
    parser.add_argument("--label", default="", help="a name for this run in the JSON file")
    parser.add_argument("--no-progress", action="store_true", help="no progress bars")
    parser.add_argument("--poll", action="store_true", help="the device polls the USB core instead of taking its irq")
    parser.add_argument("--trace", action="store_true",
                        help="measure interrupt cost, gaps and profile points (slows the device down)")
    args = parser.parse_args()

    size = parse_size(args.size)
    port = args.port or find_port()
    bench = Bench(port, not args.no_progress)
    print(f"Device on {port}: clock {bench.clock / 1e6:.0f} MHz, rings rx {bench.rx_ring} / tx {bench.tx_ring} B, "
          f"chunk {bench.chunk} B; {size} bytes per run, {args.repeat} runs per mode")
    bench.serial.write(b"irq 0\n" if args.poll else b"irq 1\n")
    print(f"USB core serviced by {'polling' if args.poll else 'interrupt'} ({bench.line()})")
    bench.serial.write(b"trace 1\n" if args.trace else b"trace 0\n")
    print(f"Interrupt tracing {'on' if args.trace else 'off'} ({bench.line()})")
    ubench = bench.micro_benchmarks()
    names = ("timer", "reg read", "ram rd 64", "ram wr 64", "ring 64", "memcpy 64", "process", "ram wr 1", "trap")
    print("Cycle costs: " + ", ".join(f"{n} {v}" for n, v in zip(names, ubench)))

    modes = MODES if args.mode == "all" else (args.mode,)
    summary: dict[str, dict] = {}
    failed = False
    for mode in modes:
        runs = [bench.run(mode, size) for _ in range(args.repeat)]
        failed |= not all(r.ok for r in runs)
        summary[mode] = {
            "host_rate": statistics.median(r.host_rate for r in runs),
            "device_rate": statistics.median(r.device_rate for r in runs),
            "host_min": min(r.host_rate for r in runs),
            "host_max": max(r.host_rate for r in runs),
            "irqs_per_packet": statistics.median(r.irqs / max(size / 64, 1) for r in runs),
            "ok": all(r.ok for r in runs),
            "runs": [asdict(r) for r in runs],
        }

    baseline = json.loads(args.compare.read_text())["modes"] if args.compare else {}
    print(f"\n{'mode':<11}{'host':>13}{'device':>13}{'of ceiling':>12}{'irq/pkt':>9}  data   change")
    for mode, s in summary.items():
        share = s["device_rate"] / (CEILING / 2 if mode == "echo" else CEILING)
        change = ""
        if mode in baseline:
            change = f"{100 * (s['host_rate'] / baseline[mode]['host_rate'] - 1):+6.1f} %"
        print(f"{mode:<11}{human(s['host_rate']):>13}{human(s['device_rate']):>13}{100 * share:>10.1f} %"
              f"{s['irqs_per_packet']:>9.2f}  {'ok  ' if s['ok'] else 'FAIL'}   {change}")
    print("(echo: each direction; the ceiling is shared, so 50 % of 1.216 MB/s each way at best)")
    if args.trace:
        print(f"\n{'mode':<11}{'isr avg':>9}{'isr max':>9}   gaps between interrupts, last run (us)")
        print(f"{'':<11}{'cycles':>9}{'cycles':>9}   {'<10':>6}{'<25':>6}{'<50':>6}{'<100':>6}{'<250':>6}"
              f"{'<500':>6}{'<1000':>6}{'>=1k':>6}")
        for mode, s in summary.items():
            last = s["runs"][-1]
            print(f"{mode:<11}{last['isr_avg']:>9}{last['isr_max']:>9}   " + "".join(f"{g:>6}" for g in last["gaps"]))
    if any(s["runs"][-1].get("profile") for s in summary.values()):
        print("\ncycles between profile points, last run (0: irq entry, 1: pending read, 2: entry irq taken,"
              "\n3: status read, 4: class callback, 5: ring copied, 6: re-armed, 7: process done)")
        print(f"{'mode':<11}" + "".join(f"{f'{k - 1}->{k}':>8}" for k in range(1, 8)))
        for mode, s in summary.items():
            print(f"{mode:<11}" + "".join(f"{v:>8}" for v in s["runs"][-1].get("profile", ())))

    if args.json:
        args.json.write_text(json.dumps({"label": args.label, "size": size, "clock": bench.clock,
                                         "time": time.strftime("%Y-%m-%d %H:%M:%S"), "ubench": dict(zip(names, ubench)),
                                         "modes": summary}, indent=2))
        print(f"saved {args.json}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
