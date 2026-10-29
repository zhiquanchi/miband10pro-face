#!/usr/bin/env python3
"""Native Xiaomi Band 10 Pro .face builder (reverse engineered).

Supports:
  - image elements (BGRA, uncompressed)
  - DigitalNum widgets backed by a shared digit image list
  - ImageList widgets (e.g. weekday / weather icons) with custom value mapping

Validated byte-for-byte against EasyFace Compiler 4.23 for:
  - background only
  - background + DigitalNum
  - background + ImageList
"""
import os
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from PIL import Image

import xwf_rle

W, H = 336, 480
HEADER_SIZE = 168
FACE_SIZE = 88
TEMPLATE = Path(__file__).with_name("header_template.bin")


def bgra(path: str, expect: Optional[tuple[int, int]] = (W, H)) -> tuple[bytes, int, int]:
    im = Image.open(path).convert("RGBA")
    if expect and im.size != expect:
        raise ValueError(f"{path}: expected {expect[0]}x{expect[1]}, got {im.width}x{im.height}")
    rgba = im.tobytes()
    out = bytearray(len(rgba))
    out[0::4] = rgba[2::4]
    out[1::4] = rgba[1::4]
    out[2::4] = rgba[0::4]
    out[3::4] = rgba[3::4]
    return bytes(out), im.width, im.height


@dataclass
class ImageElement:
    name: str
    path: str
    x: int = 0
    y: int = 0


@dataclass
class DigitWidget:
    name: str
    paths: List[str]           # 10 digit images, same size
    x: int
    y: int
    source: int                # e.g. 0x0811 for hour
    digits: int = 2
    alignment: int = 0         # 0 left, 1 center, 2 right
    spacing: int = 0


@dataclass
class ListWidget:
    name: str
    paths: List[str]           # N images, same size, ordered by source value
    x: int
    y: int
    source: int                # e.g. 0x2012 for weekday
    values: Optional[List[int]] = None   # custom value map; default 0..N-1


def _image_block(pixels: bytes, w: int, h: int) -> bytes:
    return xwf_rle.image_block(pixels, w, h)


def _list_block(images: List[tuple[bytes, int, int]]) -> tuple[bytes, int, int]:
    w, h = images[0][1], images[0][2]
    for _, iw, ih in images:
        if (iw, ih) != (w, h):
            raise ValueError("all images in a list must have the same size")
    return xwf_rle.list_block(images), w, h


def build(background: ImageElement, preview: str, widgets: List[object],
          out_path: str, title: str = "Custom", face_id: str = "167210065",
          extra_images: Optional[List[ImageElement]] = None) -> None:
    template = TEMPLATE.read_bytes()
    # Two element conventions exist in factory faces, and which one the runtime
    # honours for *data-driven* widgets is exactly what the emulator settled:
    # 0x03000000|list points straight at a raster list and draws it statically,
    # while 0x07000000|widget points at a row of the widget table, which is what
    # makes the firmware re-render the element from its bound data source.
    # clean_362150007.face (live clock in the emulator) uses the widget form.
    # Proven in the emulator (models/band10pro/userdata/fix1/b04.png): with the
    # widget form the firmware drives the elements from their data sources and
    # the face shows a live 04:47 / 100% / 10月29日 / 周四; with the list form
    # every bound element stays frozen on glyph 0. Set XWF_WIDGET_ELEMENTS=0 to
    # reproduce the old output.
    widget_elements = os.environ.get("XWF_WIDGET_ELEMENTS", "1") != "0"

    header = bytearray(template[:HEADER_SIZE])
    face = bytearray(template[HEADER_SIZE:])

    images: List[ImageElement] = [background] + (extra_images or [])
    # elements reference a raster resource: ("image", i) or ("list", i)
    elements: List[tuple] = []           # (target, x, y, kind, index)
    list_map: dict[tuple[str, ...], int] = {}
    list_blocks: List[bytes] = []
    image_blocks: List[bytes] = []
    widget_rows = []

    # image elements
    image_entries = []
    for idx, img in enumerate(images):
        px, iw, ih = bgra(img.path, (W, H) if idx == 0 else None)
        image_blocks.append(_image_block(px, iw, ih))
        image_entries.append((0x02000000 | idx, idx, len(image_blocks[-1])))
        elements.append((0x02000000 | idx, img.x, img.y, "image", idx))

    # widgets
    for w in widgets:
        key = tuple(w.paths)
        if key not in list_map:
            imgs = [bgra(p, None) for p in w.paths]
            block, _, _ = _list_block(imgs)
            list_map[key] = len(list_blocks)
            list_blocks.append(block)
        li = list_map[key]
        wi = len(widget_rows)
        if isinstance(w, ListWidget):
            default = list(range(len(w.paths)))
            values = w.values if w.values is not None else default
            explicit = values != default
            prop_size = 16 + 4 * len(values) if explicit else 16
            elements.append(((0x07000000 | wi, w.x, w.y, "widget", wi)
                             if widget_elements else
                             (0x03000000 | li, w.x, w.y, "list", li)))
            widget_rows.append((w, li, wi, prop_size, values if explicit else None))
        elif isinstance(w, DigitWidget):
            x = w.x
            cell_w = Image.open(w.paths[0]).width
            if w.alignment == 1:
                x += cell_w * w.digits // 2
            elif w.alignment == 2:
                x += cell_w * w.digits
            elements.append(((0x07000000 | wi, x, w.y, "widget", wi)
                             if widget_elements else
                             (0x03000000 | li, x, w.y, "list", li)))
            widget_rows.append((w, li, wi, 20, None))
        else:
            raise TypeError(f"unsupported widget: {w}")

    E, I, L, Wn = len(elements), len(image_entries), len(list_blocks), len(widget_rows)

    elem_table = HEADER_SIZE + FACE_SIZE
    img_table = elem_table + 16 * E
    list_table = img_table + 16 * I
    empty = list_table + 16 * L
    widget_table = empty
    button_table = widget_table + 16 * Wn
    data_start = button_table

    elem_data = data_start
    widget_props = elem_data + 16 * E
    prop_offsets = []
    acc = 0
    for row in widget_rows:
        prop_offsets.append(widget_props + acc)
        acc += row[3]
    img_data = widget_props + acc
    list_data = img_data + sum(len(b) for b in image_blocks)
    end = list_data + sum(len(b) for b in list_blocks)

    # resolve each element's raster offset / size (the device validates these)
    img_off, run = {}, img_data
    for (tag, idx, blen) in image_entries:
        img_off[idx] = (run, blen)
        run += blen
    lst_off, run = {}, list_data
    for idx, blk in enumerate(list_blocks):
        lst_off[idx] = (run, len(blk))
        run += len(blk)

    fields = {
        0: 0x80000000, 4: end,
        8: E, 12: elem_table,
        16: 0, 20: img_table,
        24: I, 28: img_table,
        32: L, 36: list_table,
        40: 0, 44: empty,
        48: 0, 52: empty,
        56: 0, 60: empty,
        64: Wn, 68: widget_table,
        72: 0, 76: button_table,
        80: 0, 84: button_table,
    }
    for off, val in fields.items():
        struct.pack_into("<I", face, off, val)

    # element table + data
    et = bytearray()
    ed = bytearray()
    for i, (target, x, y, kind, idx) in enumerate(elements):
        et += struct.pack("<IIII", i, 0, elem_data + 16 * i, 16)
        if kind == "widget":
            off = size = 0
        else:
            off, size = (img_off if kind == "image" else lst_off)[idx]
        ed += struct.pack("<IHHII", target, x, y, off, size)

    # widget table + props
    wt = bytearray()
    wp = bytearray()
    for (w, li, wi, prop_size, values), poff in zip(widget_rows, prop_offsets):
        wt += struct.pack("<IIII", 0x07000000 | wi, 0, poff, prop_size)
        if isinstance(w, ListWidget):
            prop = bytearray(prop_size)
            struct.pack_into(">H", prop, 0, w.source)
            prop[3] = 0x20
            struct.pack_into("<I", prop, 8, 0x03000000 | li)
            if values is not None:
                struct.pack_into("<I", prop, 12, 512)
                for i, v in enumerate(values):
                    struct.pack_into("<I", prop, 16 + 4 * i, v)
            wp += prop
        else:
            prop = bytearray(20)
            struct.pack_into(">H", prop, 0, w.source)
            prop[2] = w.digits
            align = 2 if w.alignment == 1 else (0 if w.alignment == 2 else 1)
            prop[3] = 0x10 | 0x04 | align
            struct.pack_into("<I", prop, 6, 1000)      # factory scale field
            struct.pack_into("<I", prop, 8, 0x03000000 | li)
            prop[13] = w.spacing
            wp += prop

    # image table
    it = bytearray()
    off = img_data
    for tag, _, blen in image_entries:
        it += struct.pack("<IIII", tag, 0, off, blen)
        off += blen
    # image list table
    lt = bytearray()
    off = list_data
    for i, b in enumerate(list_blocks):
        lt += struct.pack("<IIII", 0x03000000 | i, 0, off, len(b))
        off += len(b)

    pv_offset = end
    struct.pack_into("<I", face, 4, pv_offset)
    struct.pack_into("<I", header, 32, pv_offset)

    enc = title.encode("utf-8")
    if len(enc) > 58:
        raise ValueError("title too long")
    header[104:162] = b"\0" * 58
    header[104:104 + len(enc)] = enc
    header[40:56] = face_id.encode("ascii")[:15].ljust(16, b"\0")
    struct.pack_into("<I", header, 28, 1)
    header[6] = 1        # every factory 10 Pro face sets these two flag bytes
    header[22] = 1

    pv_pixels, pw, ph = bgra(preview)
    out = bytearray()
    out += header
    out += face
    out += et
    out += it
    out += lt
    out += b"\0" * (widget_table - (list_table + len(lt)))
    out += wt
    out += b"\0" * (button_table - (widget_table + len(wt)))
    out += ed
    out += wp
    out += b"".join(image_blocks)
    out += b"".join(list_blocks)
    out += _image_block(pv_pixels, pw, ph)
    Path(out_path).write_bytes(out)
    print(f"wrote {out_path}: {len(out)} bytes, E={E} I={I} L={L} W={Wn}")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--background", required=True)
    ap.add_argument("--preview", required=True)
    ap.add_argument("--digits", required=True, help="prefix, e.g. .../d  (d0.png..d9.png)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--title", default="TimeTest")
    args = ap.parse_args()
    paths = [f"{args.digits}{i}.png" for i in range(10)]
    build(ImageElement("bg", args.background), args.preview,
          [DigitWidget("hour", paths, 50, 50, 0x0811, 2, 0, 0)],
          args.out, args.title)
