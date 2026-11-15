"""Draw the data sheet's diagrams as SVG.

Run from anywhere: python docs/figures/src/figures.py
Writes one .svg per figure into docs/figures/. Standard library only, deterministic output.
The memory maps come from memmap (address_space.yaml, double_buffer.yaml in this folder).
"""

from pathlib import Path
from xml.sax.saxutils import escape

OUT = Path(__file__).resolve().parent.parent

FONT = "Arial, Helvetica, sans-serif"
MONO = "Consolas, 'DejaVu Sans Mono', monospace"
INK = "#1f2933"
MUTED = "#52606d"
LINE = "#3e4c59"
BLUE = "#8fa9c9"
LIGHT_BLUE = "#c9d6e8"
GREEN = "#81b29a"
LIGHT_GREEN = "#cfe6da"
ORANGE = "#e07a5f"
LIGHT_ORANGE = "#f6cdbf"
SAND = "#d8c38f"
LIGHT_SAND = "#efe5c9"
GREY = "#e4e7eb"
NOTE = "#fff6d5"


def text_width(s, size=12):
    return len(s) * size * 0.56


class Svg:
    def __init__(self, width, height):
        self.w = width
        self.h = height
        self.items = []

    def add(self, s):
        self.items.append(s)

    def rect(self, x, y, w, h, fill="#ffffff", stroke=LINE, rx=4, dash=None, sw=1.2):
        d = f' stroke-dasharray="{dash}"' if dash else ""
        self.add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{fill}" '
                 f'stroke="{stroke}" stroke-width="{sw}"{d}/>')

    def text(self, x, y, s, size=12, anchor="middle", weight="normal", color=INK, mono=False, italic=False):
        family = MONO if mono else FONT
        style = ' font-style="italic"' if italic else ""
        self.add(f'<text x="{x}" y="{y}" font-family="{family}" font-size="{size}" text-anchor="{anchor}" '
                 f'font-weight="{weight}" fill="{color}"{style}>{escape(s)}</text>')

    def lines(self, x, y, rows, size=12, anchor="middle", gap=None, **kw):
        gap = gap or size * 1.3
        top = y - (len(rows) - 1) * gap / 2 + size * 0.35
        for i, (s, opts) in enumerate(rows):
            self.text(x, top + i * gap, s, size=opts.get("size", size), anchor=anchor,
                      weight=opts.get("weight", "normal"), mono=opts.get("mono", False),
                      color=opts.get("color", INK), italic=opts.get("italic", False))

    def box(self, x, y, w, h, title, sub=(), fill=LIGHT_BLUE, mono_title=True, dash=None, rx=4):
        self.rect(x, y, w, h, fill=fill, rx=rx, dash=dash)
        rows = [(title, {"weight": "bold", "mono": mono_title})]
        rows += [(s, {"size": 10.5, "color": MUTED}) for s in sub]
        self.lines(x + w / 2, y + h / 2, rows, gap=14)

    def path(self, pts, arrow_end=True, arrow_start=False, dash=None, color=LINE, sw=1.3):
        d = "M " + " L ".join(f"{x} {y}" for x, y in pts)
        da = f' stroke-dasharray="{dash}"' if dash else ""
        me = ' marker-end="url(#arrow)"' if arrow_end else ""
        ms = ' marker-start="url(#arrow_start)"' if arrow_start else ""
        self.add(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{sw}"{da}{me}{ms}/>')

    def hop_path(self, pts, hops, r=6):
        """Polyline with a semicircle where a vertical segment jumps over a crossing line."""
        d = f"M {pts[0][0]} {pts[0][1]}"
        for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
            for hx, hy in hops:
                if x1 == x2 == hx and min(y1, y2) < hy < max(y1, y2):
                    sgn = 1 if y2 > y1 else -1
                    d += f" L {hx} {hy - sgn * r} A {r} {r} 0 0 {1 if sgn > 0 else 0} {hx} {hy + sgn * r}"
            d += f" L {x2} {y2}"
        self.add(f'<path d="{d}" fill="none" stroke="{LINE}" stroke-width="1.3" marker-end="url(#arrow)"/>')

    def line(self, x1, y1, x2, y2, color=LINE, sw=1.3, dash=None):
        self.path([(x1, y1), (x2, y2)], arrow_end=False, color=color, sw=sw, dash=dash)

    def dot(self, x, y, r=3):
        self.add(f'<circle cx="{x}" cy="{y}" r="{r}" fill="{LINE}"/>')

    def save(self, name):
        head = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w}" height="{self.h}" '
                f'viewBox="0 0 {self.w} {self.h}">\n'
                '<defs>\n'
                f'<marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" '
                f'orient="auto"><path d="M 0 0 L 10 5 L 0 10 z" fill="{LINE}"/></marker>\n'
                f'<marker id="arrow_start" viewBox="0 0 10 10" refX="1" refY="5" markerWidth="8" '
                f'markerHeight="8" orient="auto"><path d="M 10 0 L 0 5 L 10 10 z" fill="{LINE}"/></marker>\n'
                '</defs>\n'
                f'<rect x="0" y="0" width="{self.w}" height="{self.h}" fill="#ffffff"/>\n')
        (OUT / f"{name}.svg").write_text(head + "\n".join(self.items) + "\n</svg>\n", encoding="utf-8")


# ---------------------------------------------------------------------------------------------
# Block diagram
# ---------------------------------------------------------------------------------------------

def block_diagram():
    s = Svg(1050, 760)
    s.rect(80, 20, 950, 529, fill="#f5f7fa", stroke=MUTED, rx=8, dash="6 4")
    s.text(100, 42, "clk_usb domain (48 MHz)", anchor="start", weight="bold", color=MUTED)
    s.rect(80, 549, 950, 196, fill="#f7f5ee", stroke=MUTED, rx=8, dash="6 4")
    s.text(100, 735, "clk_sys domain", anchor="start", weight="bold", color=MUTED)

    W, H = 190, 58
    c = [100, 330, 560, 790]
    r = [70, 190, 300, 410, 520, 650]
    B = {
        "rx": (c[0], r[0]), "des": (c[1], r[0]), "par": (c[2], r[0]), "prot": (c[3], r[0]),
        "bus": (c[0], r[1]), "filt": (c[2], r[1]),
        "setup": (c[2], r[2]), "ep": (c[3], r[2]),
        "tx": (c[0], r[3]), "ser": (c[1], r[3]), "gen": (c[2], r[3]),
        "cdc": (c[1], r[4]), "ram": (c[2], r[4]), "regs": (c[1], r[5]),
    }
    info = {
        "rx": ("usb_phy_rx", ("4x oversampling", "line states")),
        "des": ("usb_packet_deserializer", ("SYNC, NRZI, unstuffing", "EOP, bus reset")),
        "par": ("usb_packet_parser", ("PID check", "CRC5, CRC16")),
        "prot": ("usb_protocol", ("transactions, handshakes", "toggles, double buffering")),
        "bus": ("usb_bus_state", ("suspend, resume", "remote wakeup, SOF")),
        "filt": ("usb_rx_filter", ("OUT/SETUP routing", "buffer size limit")),
        "setup": ("usb_setup_stage", ("SETUP held until", "the packet is accepted")),
        "ep": ("usb_endpoints", ("arm / complete, events", "split IN, RAM port")),
        "tx": ("usb_phy_tx", ("bit stuffing", "NRZI, EOP")),
        "ser": ("usb_packet_serializer", ("bytes to bits",)),
        "gen": ("usb_packet_generator", ("ACK/NAK/STALL", "DATA0/1, CRC16")),
        "cdc": ("usb_cdc", ("synchronizers, pulses", "handshakes")),
        "ram": ("usb_ram", ("true dual port", "2^RAM_ADDR_WIDTH bytes")),
        "regs": ("usb_device_regs", ("AXI4-Lite slave", "generated from RDL")),
    }
    fills = {"cdc": LIGHT_SAND, "ram": LIGHT_GREEN, "regs": LIGHT_SAND, "bus": LIGHT_ORANGE}
    for k, (x, y) in B.items():
        s.box(x, y, W, H, info[k][0], info[k][1], fill=fills.get(k, LIGHT_BLUE))

    def lab(x, y, t, anchor="start"):
        s.text(x, y, t, size=10, color=MUTED, anchor=anchor)

    mid = H / 2
    # receive chain
    s.path([(c[0] + W, r[0] + mid), (c[1], r[0] + mid)])
    s.path([(c[1] + W, r[0] + mid), (c[2], r[0] + mid)])
    s.path([(c[2] + W, r[0] + mid), (c[3], r[0] + mid)])
    s.path([(655, r[0] + H), (655, r[1])])
    lab(663, 160, "payload")
    s.path([(655, r[1] + H), (655, r[2])])
    s.path([(c[2] + W, r[2] + mid), (c[3], r[2] + mid)])
    s.path([(195, r[0] + H), (195, r[1])])
    lab(203, 160, "line state")
    # protocol <-> endpoints
    s.path([(900, r[0] + H), (900, r[2])], arrow_start=True)
    lab(908, 205, "route, results,")
    lab(908, 218, "armed, toggles")
    # protocol -> generator, hops over setup -> endpoints
    s.hop_path([(c[3], 118), (770, 118), (770, 425), (c[2] + W, 425)], hops=[(770, r[2] + mid)])
    lab(776, 200, "answer")
    # IN payload
    s.path([(810, r[2] + H), (810, 455), (c[2] + W, 455)])
    lab(756, 471, "IN payload")
    # transmit chain
    s.path([(c[2], r[3] + mid), (c[1] + W, r[3] + mid)])
    s.path([(c[1], r[3] + mid), (c[0] + W, r[3] + mid)])
    # endpoints <-> RAM and CDC
    s.path([(930, r[2] + H), (930, 505), (700, 505), (700, r[4])], arrow_start=True)
    lab(938, 400, "buffer data")
    s.path([(850, r[2] + H), (850, 485), (425, 485), (425, r[4])], arrow_start=True)
    lab(440, 479, "config, arm, events")
    # bus state -> CDC, hops over the transmit chain
    s.hop_path([(c[0] + W, 219), (310, 219), (310, 549), (c[1], 549)], hops=[(310, r[3] + mid)])
    # CDC <-> registers, RAM <-> registers, AXI
    s.path([(425, r[4] + H), (425, r[5])], arrow_start=True)
    s.path([(c[1] + W, 665), (655, 665), (655, r[4] + H)], arrow_start=True)
    lab(530, 658, "usb_ram window")
    s.path([(1045, 694), (c[1] + W, 694)])
    lab(1040, 687, "AXI4-Lite from CPU", anchor="end")
    # pins
    s.rect(10, 70, 60, 398, fill="#ffffff")
    s.lines(40, 269, [("D+", {"weight": "bold"}), ("D-", {"weight": "bold"}), ("", {}),
                      ("pins", {"size": 10, "color": MUTED})])
    s.path([(70, 99), (c[0], 99)])
    s.path([(c[0], r[3] + mid), (70, r[3] + mid)])
    s.path([(c[0], r[1] + mid), (70, r[1] + mid)])
    lab(85, r[1] + mid - 6, "K")
    s.save("block_diagram")


# ---------------------------------------------------------------------------------------------
# Interrupt tree
# ---------------------------------------------------------------------------------------------

def glyph(s, d, x, y, sw=1.6):
    """Draw a symbol as strokes, so PDF converters need no font that has it."""
    s.add(f'<path d="{d}" transform="translate({x} {y})" fill="none" stroke="{INK}" '
          f'stroke-width="{sw}" stroke-linecap="round" stroke-linejoin="round"/>')


GE = "M 0 -9 L 8 -5.5 L 0 -2 M 0 1.5 L 8 1.5"          # >= sign, baseline at y = 3
OHM = "M 0 0 H 2.5 A 4.2 4.6 0 1 1 6.5 0 H 9"           # omega, baseline at y = 0


def gate(s, x, y, label, h=44):
    s.rect(x, y, 44, h, fill="#ffffff", rx=2)
    cy = y + h / 2 + 5
    if label == ">=1":
        glyph(s, GE, x + 11, cy - 3)
        s.text(x + 28, cy, "1", size=14, weight="bold")
    else:
        s.text(x + 22, cy, label, size=14, weight="bold")


def interrupt_tree():
    s = Svg(1000, 420)
    rows = [
        ("endpoint[0].interrupt", "endpoint[0].interrupt_enable", "endpoint_0", 40),
        ("endpoint[1].interrupt", "endpoint[1].interrupt_enable", "endpoint_1", 120),
        ("endpoint[15].interrupt", "endpoint[15].interrupt_enable", "endpoint_15", 230),
        ("bus_event", "bus_event_enable", "bus_event", 310),
    ]
    s.text(130, 22, "per-cause registers (5 bits each)", size=11, color=MUTED, weight="bold")
    s.text(560, 22, "interrupt (summary, 17 bits)", size=11, color=MUTED, weight="bold")
    for src, en, bit, y in rows:
        s.box(20, y, 220, 26, src, fill=LIGHT_ORANGE)
        s.box(20, y + 32, 220, 26, en, fill=GREY)
        gate(s, 290, y + 7, "&")
        s.path([(240, y + 13), (290, y + 13)])
        s.path([(240, y + 45), (290, y + 45)])
        gate(s, 380, y + 7, ">=1")
        s.path([(334, y + 29), (380, y + 29)])
        s.text(357, y + 22, "5", size=9, color=MUTED)
        s.box(480, y + 16, 170, 26, f"interrupt.{bit}", fill=LIGHT_BLUE)
        s.path([(424, y + 29), (480, y + 29)])
    for x in (130, 565):
        for dy in (-8, 0, 8):
            s.dot(x, 198 + dy, r=2)

    s.rect(690, 40, 18, 328, fill=LIGHT_BLUE, rx=2)
    for y in (69, 149, 259, 339):
        s.path([(650, y), (690, y)])
    s.text(699, 30, "17", size=10, color=MUTED)
    s.box(690, 380, 200, 26, "interrupt_enable", fill=GREY)
    gate(s, 770, 190, "&", h=56)
    s.path([(708, 204), (770, 204)])
    s.path([(790, 380), (790, 246)])
    gate(s, 860, 190, ">=1", h=56)
    s.path([(814, 218), (860, 218)])
    s.text(837, 211, "17", size=9, color=MUTED)
    s.path([(904, 218), (975, 218)])
    s.text(945, 208, "irq", weight="bold", mono=True)
    s.save("interrupt_tree")


# ---------------------------------------------------------------------------------------------
# External circuit
# ---------------------------------------------------------------------------------------------

def resistor_v(s, x, y1, y2, label, dash=None):
    mid = (y1 + y2) / 2
    s.line(x, y1, x, mid - 22, dash=dash)
    s.rect(x - 8, mid - 22, 16, 44, fill="#ffffff", rx=1, dash=dash)
    s.line(x, mid + 22, x, y2, dash=dash)
    s.text(x + 14, mid + 4, label, size=11, anchor="start")


def resistor_h(s, x1, x2, y, label, dash=None):
    mid = (x1 + x2) / 2
    s.line(x1, y, mid - 22, y, dash=dash)
    s.rect(mid - 22, y - 8, 44, 16, fill="#ffffff", rx=1, dash=dash)
    s.line(mid + 22, y, x2, y, dash=dash)
    s.text(mid, y - 14, label, size=11)


def ground(s, x, y):
    s.line(x, y, x, y + 8)
    s.line(x - 10, y + 8, x + 10, y + 8, sw=1.6)
    s.line(x - 6, y + 12, x + 6, y + 12, sw=1.4)
    s.line(x - 2, y + 16, x + 2, y + 16, sw=1.2)


def external_circuit():
    s = Svg(820, 450)
    s.rect(20, 40, 130, 360, fill=GREY, rx=6)
    s.text(85, 30, "USB connector", weight="bold")
    s.rect(580, 40, 220, 360, fill=LIGHT_BLUE, rx=6)
    s.text(690, 30, "FPGA (3.3 V I/O bank)", weight="bold")
    for name, y in {"VBUS": 70, "D+": 260, "D-": 310, "GND": 370}.items():
        s.text(140, y + 4, name, anchor="end", weight="bold")
    for name, y in {"usb_vbus": 140, "usb_pu": 180, "usb_dp": 260, "usb_dn": 310}.items():
        s.text(592, y + 4, name, anchor="start", mono=True, weight="bold")
        s.rect(576, y - 4, 8, 8, fill=LINE, rx=0)
    s.text(690, 370, "usb_top", mono=True, color=MUTED)

    s.line(150, 70, 260, 70)
    resistor_v(s, 260, 70, 140, "R1")
    s.dot(260, 140)
    resistor_v(s, 260, 140, 210, "R2")
    ground(s, 260, 210)
    s.line(260, 140, 576, 140)
    s.text(300, 132, "divider: valid logic high, never above 3.3 V", size=10.5, color=MUTED, anchor="start")

    s.line(420, 180, 576, 180)
    resistor_v(s, 420, 180, 260, "")
    s.text(434, 224, "1.5 k", size=11, anchor="start")
    glyph(s, OHM, 461, 224, sw=1.2)
    s.dot(420, 260)
    s.line(150, 260, 460, 260)
    resistor_h(s, 460, 560, 260, "Rs (opt.)", dash="4 3")
    s.line(560, 260, 576, 260)
    s.line(150, 310, 460, 310)
    resistor_h(s, 460, 560, 310, "Rs (opt.)", dash="4 3")
    s.line(560, 310, 576, 310)
    s.line(150, 370, 200, 370)
    ground(s, 200, 370)
    s.text(410, 430, "Rs: optional series resistors near the FPGA (some tens of ohms) to match the cable",
           size=10.5, color=MUTED)
    s.save("external_circuit")


# ---------------------------------------------------------------------------------------------
# Buffer ownership
# ---------------------------------------------------------------------------------------------

def buffer_ownership():
    s = Svg(820, 300)
    s.rect(60, 90, 220, 90, fill=LIGHT_GREEN, rx=40)
    s.lines(170, 135, [("Software owns", {"weight": "bold"}), ("status.armed = 0", {"mono": True, "size": 11})])
    s.rect(540, 90, 220, 90, fill=LIGHT_ORANGE, rx=40)
    s.lines(650, 135, [("Hardware owns", {"weight": "bold"}), ("status.armed = 1", {"mono": True, "size": 11})])
    s.path([(280, 115), (540, 115)])
    s.lines(410, 92, [("software writes transfer.arm = 1", {"mono": True, "size": 11}),
                      ("(IN: with length, in the same write)", {"size": 10.5, "color": MUTED})])
    s.path([(540, 155), (280, 155)])
    s.lines(410, 190, [("transfer completes:", {"weight": "bold", "size": 11}),
                       ("armed = 0, byte_count updated,", {"mono": True, "size": 11}),
                       ("interrupt.transfer_complete = 1", {"mono": True, "size": 11}),
                       ("all in the same clock cycle", {"size": 10.5, "color": MUTED})])
    s.path([(130, 90), (130, 40), (210, 40), (210, 90)])
    s.text(170, 30, "OUT packet: NAK + overrun", size=10.5, color=MUTED)
    s.path([(610, 90), (610, 40), (690, 40), (690, 90)])
    s.text(650, 30, "IN without ACK: resend", size=10.5, color=MUTED)
    s.path([(650, 180), (650, 260), (170, 260), (170, 180)], dash="5 3")
    s.text(410, 280, "SETUP on a control entry: clears armed on both EP0 entries", size=10.5, color=MUTED)
    s.save("buffer_ownership")


# ---------------------------------------------------------------------------------------------
# Software layers
# ---------------------------------------------------------------------------------------------

def sw_layers():
    s = Svg(860, 430)
    layers = [
        ("application", "sw/app/cdc_echo, sw/app/cdc_bench", "#ffffff"),
        ("L4  classes", "usbd_class.h, usbd_cdc_acm.h/.c", LIGHT_SAND),
        ("L3  device core", "usbd.h, usbd_core.c, usbd_ctrl.c, usbd_std.c", LIGHT_GREEN),
        ("L2  HAL", "usb_hal.h/.c", LIGHT_BLUE),
        ("L1  registers", "usb_ll.h, usb_ram.h/.c, generated usb_device_regs.h", LIGHT_BLUE),
    ]
    y = 20
    for name, files, fill in layers:
        s.rect(20, y, 560, 52, fill=fill)
        s.text(40, y + 31, name, anchor="start", weight="bold")
        s.text(200, y + 31, files, anchor="start", mono=True, size=11)
        y += 62
    s.path([(300, y - 10), (300, y + 30)], arrow_start=True)
    s.text(310, y + 15, "AXI4-Lite: registers and shared RAM", size=10.5, color=MUTED, anchor="start")
    s.rect(20, y + 30, 560, 52, fill=GREY)
    s.text(40, y + 61, "hardware", anchor="start", weight="bold")
    s.text(200, y + 61, "usb_top", anchor="start", mono=True)
    s.rect(640, 82, 200, 114, fill=LIGHT_ORANGE)
    s.lines(740, 139, [("port", {"weight": "bold"}), ("usb_port.h", {"mono": True, "size": 11}),
                       ("port/neorv32/", {"mono": True, "size": 11}), ("", {}),
                       ("irq mask, critical sections", {"size": 10.5, "color": MUTED})])
    s.path([(640, 108), (580, 108)])
    s.path([(640, 170), (580, 170)])
    s.save("sw_layers")


# ---------------------------------------------------------------------------------------------
# Reference system
# ---------------------------------------------------------------------------------------------

def reference_system():
    s = Svg(900, 300)
    s.box(20, 110, 120, 60, "PLL", ("100 MHz in",), fill=GREY)
    s.box(220, 30, 200, 70, "NEORV32", ("RV32IM, no caches", "clk_sys = 60 MHz"), fill=LIGHT_GREEN, mono_title=False)
    s.box(220, 180, 200, 60, "XBUS to AXI4-Lite", ("bridge",), fill=GREY, mono_title=False)
    s.box(520, 110, 200, 80, "usb_top", ("0xF0000000, 8 KiB", "clk_usb = 48 MHz"), fill=LIGHT_BLUE)
    s.box(790, 110, 90, 80, "Pmod JB", ("D+, D-", "pull-up, VBUS"), fill=GREY, mono_title=False)
    s.path([(140, 125), (180, 125), (180, 65), (220, 65)])
    s.text(184, 58, "clk_sys", size=10, color=MUTED, anchor="start")
    s.path([(140, 155), (180, 155), (180, 270), (620, 270), (620, 190)])
    s.text(400, 263, "clk_usb", size=10, color=MUTED)
    s.path([(320, 100), (320, 180)])
    s.text(328, 145, "XBUS", size=10, color=MUTED, anchor="start")
    s.path([(420, 210), (470, 210), (470, 170), (520, 170)])
    s.text(474, 200, "AXI4-Lite", size=10, color=MUTED, anchor="start")
    s.path([(520, 130), (470, 130), (470, 65), (420, 65)])
    s.text(464, 100, "irq", size=10, color=MUTED, anchor="end", mono=True)
    s.text(464, 113, "(machine external)", size=9, color=MUTED, anchor="end")
    s.path([(720, 150), (790, 150)], arrow_start=True)
    s.save("reference_system")


# ---------------------------------------------------------------------------------------------
# Sequence diagrams
# ---------------------------------------------------------------------------------------------

PARTICIPANTS = {"host": ("USB host", GREY), "hw": ("usb_top", LIGHT_BLUE), "sw": ("Software (CPU)", LIGHT_GREEN)}
XPOS = {"host": 110, "hw": 430, "sw": 750}


def sequence(name, steps, title=None):
    """steps: ("msg", src, dst, label[, style]) | ("note", who, text) | ("gap", label)."""
    row = 30
    heights = []
    for st in steps:
        if st[0] == "note":
            heights.append(18 + 15 * len(st[2].split("\n")))
        else:
            heights.append(row)
    top = 70
    height = top + sum(heights) + 70
    s = Svg(860, height)
    for key, (label, fill) in PARTICIPANTS.items():
        x = XPOS[key]
        for y in (16, height - 46):
            s.rect(x - 75, y, 150, 32, fill=fill)
            s.text(x, y + 21, label, weight="bold")
        s.line(x, 48, x, height - 46, color="#9aa5b1", dash="4 4", sw=1)
    y = top
    for st, h in zip(steps, heights):
        kind = st[0]
        if kind == "msg":
            _, a, b, label, *rest = st
            style = rest[0] if rest else "solid"
            x1, x2 = XPOS[a], XPOS[b]
            yy = y + h - 8
            if style == "lost":
                end = x1 + (x2 - x1) * 0.55
                s.path([(x1, yy), (end, yy)], arrow_end=False)
                s.line(end - 6, yy - 6, end + 6, yy + 6, color=ORANGE, sw=2)
                s.line(end - 6, yy + 6, end + 6, yy - 6, color=ORANGE, sw=2)
            else:
                s.path([(x1, yy), (x2 + (-3 if x2 > x1 else 3), yy)], dash="5 3" if style == "dashed" else None)
            s.text((x1 + x2) / 2, yy - 6, label, size=11, mono=style != "irq")
        elif kind == "note":
            _, who, text = st
            rows = text.split("\n")
            w = max(text_width(r, 11) for r in rows) + 20
            x = XPOS[who] - w / 2
            s.rect(x, y + 4, w, h - 8, fill=NOTE, stroke="#c9b26b", rx=2)
            s.lines(XPOS[who], y + h / 2, [(r, {"size": 11}) for r in rows], gap=15)
        elif kind == "gap":
            s.text(430, y + 20, st[1], size=10.5, color=MUTED, italic=True)
        y += h
    if title:
        pass
    s.save(name)


def seq_out_transfer():
    sequence("seq_out_transfer", [
        ("msg", "sw", "hw", "transfer.arm = 1"),
        ("msg", "host", "hw", "OUT token"),
        ("msg", "host", "hw", "DATA0, n bytes"),
        ("msg", "hw", "host", "ACK", "dashed"),
        ("note", "hw", "write buffer\narmed = 0, byte_count = n"),
        ("msg", "hw", "sw", "irq: transfer_complete", "irq"),
        ("msg", "sw", "hw", "read byte_count, buffer; clear interrupt"),
        ("msg", "host", "hw", "OUT token, DATA1"),
        ("msg", "hw", "host", "NAK (not armed)", "dashed"),
        ("note", "hw", "data dropped, overrun = 1"),
        ("msg", "sw", "hw", "transfer.arm = 1"),
        ("msg", "host", "hw", "OUT token, DATA1 (retry)"),
        ("msg", "hw", "host", "ACK", "dashed"),
        ("msg", "hw", "sw", "irq: transfer_complete", "irq"),
    ])


def seq_in_split():
    sequence("seq_in_split", [
        ("note", "sw", "size = 192 B buffer, split:\n64-byte packets"),
        ("msg", "sw", "hw", "write 192 bytes into the buffer"),
        ("msg", "sw", "hw", "transfer = arm | length 192"),
        ("msg", "host", "hw", "IN token"),
        ("msg", "hw", "host", "DATA0, bytes 0-63", "dashed"),
        ("msg", "host", "hw", "ACK"),
        ("msg", "host", "hw", "IN token"),
        ("msg", "hw", "host", "DATA1, bytes 64-127", "dashed"),
        ("msg", "host", "hw", "ACK", "lost"),
        ("msg", "host", "hw", "IN token"),
        ("msg", "hw", "host", "DATA1, bytes 64-127 (resent)", "dashed"),
        ("msg", "host", "hw", "ACK"),
        ("msg", "host", "hw", "IN token"),
        ("msg", "hw", "host", "DATA0, bytes 128-191", "dashed"),
        ("msg", "host", "hw", "ACK"),
        ("note", "hw", "last packet ACKed: armed = 0"),
        ("msg", "hw", "sw", "irq: transfer_complete (one per arm)", "irq"),
    ])


def seq_control_read():
    sequence("seq_control_read", [
        ("msg", "host", "hw", "SETUP token, DATA0 (8 bytes)"),
        ("msg", "hw", "host", "ACK", "dashed"),
        ("note", "hw", "write 8 bytes to EP0 OUT buffer\nclear stall and armed on EP0\nnext PID DATA1"),
        ("msg", "hw", "sw", "irq: setup (EP0 OUT)", "irq"),
        ("msg", "sw", "hw", "read SETUP: GET_DESCRIPTOR, wLength 18"),
        ("msg", "sw", "hw", "EP0 IN: write 18 bytes, arm | length 18"),
        ("msg", "sw", "hw", "EP0 OUT: arm (status stage, early)"),
        ("msg", "host", "hw", "IN token"),
        ("msg", "hw", "host", "DATA1, 18 bytes", "dashed"),
        ("msg", "host", "hw", "ACK"),
        ("msg", "hw", "sw", "irq: transfer_complete (EP0 IN)", "irq"),
        ("msg", "host", "hw", "OUT token, DATA1 (0 bytes)"),
        ("msg", "hw", "host", "ACK", "dashed"),
        ("msg", "hw", "sw", "irq: transfer_complete + zlp (EP0 OUT)", "irq"),
    ])


def seq_double_buffer_out():
    sequence("seq_double_buffer_out", [
        ("note", "sw", "entries n, n+1 paired\n(double_buffer on n)"),
        ("msg", "sw", "hw", "arm entry n, arm entry n+1"),
        ("msg", "host", "hw", "OUT, DATA0"),
        ("msg", "hw", "host", "ACK (into entry n)", "dashed"),
        ("msg", "hw", "sw", "irq: transfer_complete (n)", "irq"),
        ("msg", "host", "hw", "OUT, DATA1"),
        ("msg", "hw", "host", "ACK (into entry n+1)", "dashed"),
        ("note", "sw", "reads entry n meanwhile"),
        ("msg", "sw", "hw", "re-arm entry n"),
        ("msg", "hw", "sw", "irq: transfer_complete (n+1)", "irq"),
        ("msg", "host", "hw", "OUT, DATA0"),
        ("msg", "hw", "host", "ACK (into entry n)", "dashed"),
        ("msg", "sw", "hw", "re-arm entry n+1"),
        ("gap", "no NAK while software keeps one buffer ahead"),
    ])


def seq_remote_wakeup():
    sequence("seq_remote_wakeup", [
        ("note", "host", "bus idle (J)"),
        ("note", "hw", "3 ms idle: suspended = 1"),
        ("msg", "hw", "sw", "irq: bus_event.suspend", "irq"),
        ("note", "sw", "reduce power"),
        ("msg", "sw", "hw", "control.remote_wakeup = 1"),
        ("note", "hw", "wait until bus idle 5 ms"),
        ("msg", "hw", "host", "drive K for 2 ms", "dashed"),
        ("msg", "host", "hw", "resume K, then EOP"),
        ("note", "hw", "suspended = 0"),
        ("msg", "hw", "sw", "irq: bus_event.resume", "irq"),
        ("msg", "host", "hw", "SOF, traffic resumes"),
    ])


# ---------------------------------------------------------------------------------------------
# Logo (same palette as the usb-gui icon)
# ---------------------------------------------------------------------------------------------

LOGO_BG = "#1E1E2E"
LOGO_METAL = "#A6ADC8"
LOGO_BLUE = "#89B4FA"


def logo_mark(x=0, y=0, scale=1.0, background=True):
    """The 256 x 256 mark: a USB plug whose body is a chip with pins and a square wave."""
    parts = [f'<g transform="translate({x} {y}) scale({scale})">']
    if background:
        parts.append(f'<rect width="256" height="256" rx="50" fill="{LOGO_BG}"/>')
    parts += [
        f'<rect x="80" y="30" width="96" height="84" rx="10" fill="{LOGO_METAL}"/>',
        f'<rect x="98" y="58" width="22" height="22" rx="4" fill="{LOGO_BG}"/>',
        f'<rect x="136" y="58" width="22" height="22" rx="4" fill="{LOGO_BG}"/>',
    ]
    for py in (128, 154, 180, 206):
        parts.append(f'<rect x="34" y="{py}" width="28" height="12" rx="3" fill="{LOGO_METAL}"/>')
        parts.append(f'<rect x="194" y="{py}" width="28" height="12" rx="3" fill="{LOGO_METAL}"/>')
    parts += [
        f'<rect x="56" y="112" width="144" height="118" rx="14" fill="{LOGO_BLUE}"/>',
        f'<polyline points="56,176 88,176 88,142 128,142 128,206 168,206 168,176 200,176" fill="none" '
        f'stroke="{LOGO_BG}" stroke-width="12" stroke-linejoin="round" stroke-linecap="butt"/>',
        '</g>',
    ]
    return "\n".join(parts)


def write_svg(name, w, h, body):
    (OUT / f"{name}.svg").write_text(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">\n'
        f'{body}\n</svg>\n', encoding="utf-8")


def logo():
    write_svg("logo", 256, 256, logo_mark())
    write_svg("favicon", 256, 256, logo_mark())


def logo_wide():
    body = [logo_mark(0, 0, 0.5)]
    body.append(f'<text x="150" y="58" font-family="{FONT}" font-size="40" font-weight="bold" '
                f'fill="{LOGO_BG}">usb-device</text>')
    body.append(f'<text x="152" y="92" font-family="{FONT}" font-size="17" fill="{MUTED}">'
                f'USB 2.0 Full-Speed Device Controller</text>')
    write_svg("logo_wide", 520, 128, "\n".join(body))


def social_preview():
    body = [f'<rect width="1280" height="640" fill="{LOGO_BG}"/>', logo_mark(100, 128, 1.5, background=False)]
    body.append(f'<text x="560" y="262" font-family="{FONT}" font-size="64" font-weight="bold" '
                f'fill="#CDD6F4">usb-device</text>')
    body.append(f'<text x="562" y="327" font-family="{FONT}" font-size="34" fill="{LOGO_BLUE}">'
                f'USB 2.0 Full-Speed Device Controller</text>')
    for i, line in enumerate(("VHDL-2008 core with a digital PHY, no transceiver chip",
                              "AXI4-Lite registers, portable C stack, CDC-ACM",
                              "Verified with cocotb and USBCV Chapter 9 tests")):
        body.append(f'<text x="562" y="{397 + i * 42}" font-family="{FONT}" font-size="26" '
                    f'fill="{LOGO_METAL}">{escape(line)}</text>')
    write_svg("social_preview", 1280, 640, "\n".join(body))


if __name__ == "__main__":
    logo()
    logo_wide()
    social_preview()
    block_diagram()
    interrupt_tree()
    external_circuit()
    buffer_ownership()
    sw_layers()
    reference_system()
    seq_out_transfer()
    seq_in_split()
    seq_control_read()
    seq_double_buffer_out()
    seq_remote_wakeup()
