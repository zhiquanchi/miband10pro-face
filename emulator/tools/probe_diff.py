#!/usr/bin/env python3
"""Turn probe-face screenshots into a per-source change table.

`native/xwf_probe.py` lays candidate data sources on a 4x9 grid (84x53 px cells,
tile index i at column i%4, row i//4). Boot that face in the emulator, take
screens several minutes apart, and this says which cells actually moved — which
is how you tell a live data source from one that is frozen at glyph 0 without
having to read a single digit off a 336x480 screenshot.

  python3 tools/probe_diff.py <a.png> <b.png> [...] [--legend legend.txt]

`--legend` is the "tile index -> source id" listing xwf_probe.py prints; pass it
so the output names sources instead of tile numbers.
"""
import argparse
import re
import sys
from pathlib import Path

COLS, ROWS = 4, 9
CW, CH = 336 // COLS, 480 // ROWS
# The label line occupies the top of each cell; the digits below it are what we
# actually care about, so compare only the lower band.
LABEL_BAND = 20


def load(path):
    """Return the image as a list of rows of packed pixel tuples, via PIL or ppm."""
    p = Path(path)
    try:
        from PIL import Image
        im = Image.open(p).convert("L")
        return im.size, list(im.getdata()), im.width
    except ImportError:
        pass
    # Fall back to the PPM the emulator wrote (same base name, .ppm).
    ppm = p.with_suffix(".ppm")
    if not ppm.exists():
        sys.exit(f"need Pillow (or a sibling .ppm) to decode {p}")
    data = ppm.read_bytes()
    # P6\n<w> <h>\n<maxval>\n<pixels>
    head = data.split(b"\n", 3)
    dims = head[1].split()
    w, h = int(dims[0]), int(dims[1])
    raw = head[3]
    gray = [sum(raw[i:i + 3]) // 3 for i in range(0, w * h * 3, 3)]
    return (w, h), gray, w


def cells(size, gray, width):
    out = []
    for i in range(COLS * ROWS):
        col, row = i % COLS, i // COLS
        x0, y0 = col * CW, row * CH + LABEL_BAND
        block = []
        for y in range(y0, min(y0 + CH - LABEL_BAND, size[1])):
            base = y * width
            block.append(gray[base + x0:base + x0 + CW])
        out.append(block)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("shots", nargs="+")
    ap.add_argument("--legend", type=Path)
    ap.add_argument("--threshold", type=int, default=3,
                    help="min number of pixels that must differ by >8 grey levels")
    args = ap.parse_args()

    legend = {}
    if args.legend and args.legend.exists():
        for m in re.finditer(r"^\s*(\d+)\s+([0-9a-fA-F]{4})\s+(.*)$", args.legend.read_text(), re.M):
            legend[int(m.group(1))] = (m.group(2), m.group(3).strip())

    grids = []
    for s in args.shots:
        size, gray, width = load(s)
        grids.append(cells(size, gray, width))

    print("shots:", " ".join(Path(s).name for s in args.shots))
    # Digits are dark grey on a near-black panel, so a *mean* delta buries the
    # signal. Count pixels that actually moved instead.
    moved = {}
    for i in range(COLS * ROWS):
        scores = []
        for a, b in zip(grids, grids[1:]):
            changed = sum(1 for ra, rb in zip(a[i], b[i])
                          for x, y in zip(ra, rb) if abs(x - y) > 8)
            scores.append(changed)
        if any(s >= args.threshold for s in scores):
            moved[i] = scores

    if not moved:
        print("no cell changed between the shots -> none of these sources is live here")
        return 0
    print(f"\n{len(moved)} of {COLS * ROWS} cells changed:")
    for i in sorted(moved, key=lambda k: -max(moved[k])):
        sid, note = legend.get(i, ("?", "?"))
        print(f"   tile {i:2d}  {sid}  {note:18s} changed px per step {moved[i]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
