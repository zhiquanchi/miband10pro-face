#!/usr/bin/env python3
"""Generate a probe watch face that reveals what Band 10 Pro's undocumented
data-source IDs actually carry.

Every candidate source gets one 3-digit tile on a labelled grid; install the
face once and read the band. Known-good sources (battery / steps / time / date)
are included as controls so a blank grid can be told apart from a dead source.

  python3 xwf_probe.py --out probe.face --preview assets/preview.png

Only 2-byte source IDs are probed: the DigitalNum property stores the source as
one big-endian u16, so the long IDs ("1000911", "40009031") need a different
encoding and are left for a second pass.
"""
import argparse
import math
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from xwf_build import ImageElement, DigitWidget, build
from xwf_face import font, digit_metrics, make_digit_set

W, H = 336, 480
BG = (12, 16, 20, 255)
LABEL = (120, 200, 180, 255)
DIGIT = (238, 246, 246, 255)
CTRL = (255, 196, 120, 255)

# (id, note, is_control) — the 36 sources Xiaomi's own faces actually reference
SOURCES = [
    ("0841", "battery CTRL", True),
    ("0821", "steps CTRL", True),
    ("0811", "hour CTRL", True),
    ("1012", "month CTRL", True),
    ("2031", "wx temp CTRL", True),
    ("0813", "ampm", False),
    ("1811", "second", False),
    ("1011", "minute", False),
    ("1812", "day", False),
    ("2012", "weekday", False),
    ("0822", "hrm", False),
    ("0823", "calories", False),
    ("0824", "activity/stand", False),
    ("0825", "unknown 25", False),
    ("0826", "stress", False),
    ("0828", "sleep", False),
    ("0829", "unknown 29", False),
    ("1021", "steps percent", False),
    ("1022", "hrm interval", False),
    ("1023", "cal percent", False),
    ("1024", "unknown 24", False),
    ("1029", "unknown 29b", False),
    ("1821", "unknown 21b", False),
    ("1823", "unknown 23b", False),
    ("1824", "unknown 24b", False),
    ("1829", "unknown 29c", False),
    ("2021", "unknown 21c", False),
    ("1013", "unknown 13a", False),
    ("2013", "unknown 13b", False),
    ("3012", "date fam 30", False),
    ("3812", "date fam 38", False),
    ("6812", "date fam 68", False),
    ("3031", "wx type", False),
    ("5031", "wx air", False),
    ("7831", "wx fam 78", False),
    ("b031", "wx fam b0", False),
]
COLS, ROWS = 4, 9


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="probe.face")
    ap.add_argument("--preview", default=str(Path(__file__).resolve().parent / "assets/preview.png"))
    ap.add_argument("--title", default="PROBE")
    args = ap.parse_args()

    cw, ch = 336 // COLS, 480 // ROWS
    bg = Image.new("RGBA", (W, H), BG)
    d = ImageDraw.Draw(bg)
    lf = font(9, "/System/Library/Fonts/SFNSMono.ttf") if Path(
        "/System/Library/Fonts/SFNSMono.ttf").exists() else font(9)
    tf = font(8)

    with tempfile.TemporaryDirectory(prefix="xwfprobe-") as td:
        tmp = Path(td)
        f = font(20)
        dw, dh, ascent = digit_metrics(f)
        paths, dcw, dch = make_digit_set(tmp, "p", 20, DIGIT)

        widgets = []
        legend = []
        for i, (sid, note, ctrl) in enumerate(SOURCES):
            col, row = i % COLS, i // COLS
            x, y = col * cw, row * ch
            d.line([(x, y), (x + cw, y)], fill=(40, 48, 54, 255))
            d.line([(x, y), (x, y + ch)], fill=(40, 48, 54, 255))
            d.text((x + 3, y + 2), sid, font=tf, fill=CTRL if ctrl else LABEL)
            d.text((x + 3, y + 12), note[:13], font=tf, fill=(90, 105, 112, 255))
            widgets.append(DigitWidget(f"s{sid}", paths, x + 3, y + 24, int(sid, 16), 3, 0, 0))
            legend.append((i, sid, note, f"col{col} row{row}"))

        bgpath = tmp / "probe_bg.png"
        bg.save(bgpath)
        build(ImageElement("bg", str(bgpath)), args.preview, widgets, args.out,
              title=args.title)

    print(f"\nlegend (tile index -> source id -> grid cell):")
    for i, sid, note, cell in legend:
        print(f"  {i:2d}  {sid}  {note:16s} {cell}")


if __name__ == "__main__":
    main()
