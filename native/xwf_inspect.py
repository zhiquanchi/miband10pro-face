#!/usr/bin/env python3
"""Dump the internal structure of a Xiaomi .face container.

Layout (reverse engineered, see xwf_build.py):
  0..167   global header (168 B)
  168..255 face entry (88 B) -- table counts + offsets live here
  ...      element / image / imagelist / widget tables
  ...      element data + widget props + image blocks + imagelist blocks
  end      preview image block

Usage:  python3 xwf_inspect.py a.face [b.face ...]
"""
import struct
import sys
from pathlib import Path

HEADER_SIZE = 168
FACE_SIZE = 88

# (offset, name) pairs inside the 88-byte face entry, as written by xwf_build.build()
FACE_FIELDS = [
    (0, "elem_count"), (4, "preview_end"), (8, "count_E"), (12, "elem_table"),
    (16, "f16"), (20, "img_table"), (24, "count_I"), (28, "img_table2"),
    (32, "count_L"), (36, "list_table"), (40, "f40"), (44, "empty44"),
    (48, "f48"), (52, "empty52"), (56, "f56"), (60, "empty60"),
    (64, "count_W"), (68, "widget_table"), (72, "f72"), (76, "button_table"),
    (80, "f80"), (84, "button_table2"),
]


def u32(b, o):
    return struct.unpack_from("<I", b, o)[0]


def table(b, off, count, label):
    rows = []
    for i in range(count):
        o = off + 16 * i
        if o + 16 > len(b):
            rows.append((i, "TRUNCATED", 0, 0, 0))
            continue
        a, c, d, e = struct.unpack_from("<IIII", b, o)
        rows.append((i, a, c, d, e))
    print(f"  -- {label} @ {off} x{count}")
    for i, a, c, d, e in rows:
        print(f"     [{i:2d}] tag=0x{a:08x} f1={c} off={d} size={e}")
    return rows


def image_block(b, off):
    """Return (w, h, payload_len, kind) for a 12-byte block header."""
    hdr = b[off:off + 12]
    w, h, ln = struct.unpack_from("<HHI", hdr, 4)
    kind = "BGRA-raw" if ln == w * h * 4 else ("RGBA-raw" if ln == w * h * 4 else f"len={ln}")
    return w, h, ln, hdr[:4].hex()


def inspect(path):
    b = Path(path).read_bytes()
    print(f"\n===== {path}  ({len(b)} bytes) =====")
    print(f"  magic      {b[:4].hex()}   byte[5]={b[5]}")
    print(f"  face_id    {b[40:56].split(chr(0).encode())[0]!r}")
    print(f"  title      {b[104:162].split(chr(0).encode())[0]!r}")
    print(f"  hdr u32s   " + " ".join(f"{i}:{u32(b, i)}" for i in range(0, 40, 4)))
    print(f"             " + " ".join(f"{i}:{u32(b, i)}" for i in range(56, 104, 4)))

    fb = b[HEADER_SIZE:HEADER_SIZE + FACE_SIZE]
    print("  face entry:")
    for off, name in FACE_FIELDS:
        print(f"     {off:3d} {name:14s} = {u32(fb, off)}")

    count_E = u32(fb, 8)
    elem_table = u32(fb, 12)
    img_table = u32(fb, 20)
    count_I = u32(fb, 24)
    list_table = u32(fb, 36)
    count_L = u32(fb, 32)
    count_W = u32(fb, 64)
    widget_table = u32(fb, 68)
    button_table = u32(fb, 76)
    preview_end = u32(fb, 4)

    table(b, elem_table, count_E, "element table")
    table(b, img_table, count_I, "image table")
    table(b, list_table, count_L, "imagelist table")
    if widget_table != list_table + 16 * count_L:
        print(f"  !! gap between imagelist table end "
              f"{list_table + 16 * count_L} and widget table {widget_table}")
    table(b, widget_table, count_W, "widget table")

    # element data + widget props (element data is referenced by the element table)
    ed = struct.unpack_from("<I", b, elem_table + 8)[0] if count_E else elem_table + 16 * count_E
    print(f"  -- element data @ {ed}")
    for i in range(count_E):
        o = ed + 16 * i
        target, x, y, doff, dsize = struct.unpack_from("<IHHII", b, o)
        print(f"     [{i:2d}] target=0x{target:08x} x={x} y={y} res_off={doff} res_size={dsize}")

    wp = ed + 16 * count_E
    print(f"  -- widget props @ {wp}")
    for i in range(count_W):
        o = widget_table + 16 * i
        _tag, _f1, poff, psize = struct.unpack_from("<IIII", b, o)
        print(f"     [{i:2d}] off={poff} size={psize} data={b[poff:poff + psize].hex()}")

    # image blocks
    for i in range(count_I):
        o = img_table + 16 * i
        _tag, _f1, off, size = struct.unpack_from("<IIII", b, o)
        w, h, ln, hdrhex = image_block(b, off)
        print(f"  img[{i}] @ {off} size={size} hdr={hdrhex} {w}x{h} payload={ln} "
              f"{'RAW-BGRA' if ln == w * h * 4 else 'COMPRESSED/OTHER'}")
    for i in range(count_L):
        o = list_table + 16 * i
        _tag, _f1, off, size = struct.unpack_from("<IIII", b, o)
        hdr = b[off:off + 12]
        n = hdr[1]
        w, h, ln = struct.unpack_from("<HHI", hdr, 4)
        print(f"  lst[{i}] @ {off} size={size} n={n} {w}x{h} payload={ln} "
              f"per={ln // max(n, 1)} {'RAW-BGRA' if ln == n * w * h * 4 else 'OTHER'}")

    if preview_end and preview_end < len(b):
        w, h, ln, hdrhex = image_block(b, preview_end)
        print(f"  preview @ {preview_end} hdr={hdrhex} {w}x{h} payload={ln} "
              f"{'RAW-BGRA' if ln == w * h * 4 else 'OTHER'}  tail={len(b) - preview_end - 12 - ln}")
    print(f"  declared preview_end={preview_end}  file={len(b)}")


if __name__ == "__main__":
    for p in sys.argv[1:] or ["design.face"]:
        inspect(p)
