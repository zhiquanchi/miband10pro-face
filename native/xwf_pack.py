#!/usr/bin/env python3
"""Native Xiaomi Band 10 Pro .face packer (background-only, reverse engineered).

This reproduces the official EasyFace compiler output for a single background
image + preview image, without Wine.

Layout (336x480 BGRA, uncompressed):
  168-byte global header
   88-byte face entry
   16-byte element table
   16-byte image table
   12-byte image block header + BGRA pixels
   12-byte preview header + BGRA pixels

Usage:
  python3 xwf_pack.py --background bg.png --preview preview.png --out out.face
"""
import argparse
import struct
from pathlib import Path

from PIL import Image

W, H = 336, 480
HEADER_SIZE = 168
FACE_SIZE = 88
TEMPLATE = Path(__file__).with_name("header_template.bin")


def bgra(path: str) -> bytes:
    im = Image.open(path).convert("RGBA")
    if im.size != (W, H):
        raise ValueError(f"{path}: expected {W}x{H}, got {im.width}x{im.height}")
    rgba = im.tobytes()
    out = bytearray(len(rgba))
    out[0::4] = rgba[2::4]  # B
    out[1::4] = rgba[1::4]  # G
    out[2::4] = rgba[0::4]  # R
    out[3::4] = rgba[3::4]  # A
    return bytes(out)


def image_block(pixels: bytes) -> bytes:
    assert len(pixels) == W * H * 4
    return struct.pack("<4xHH I", W, H, len(pixels)) + pixels


def pack(background: str, preview: str, out_path: str, title: str = "Custom",
         face_id: str = "167210065") -> None:
    bg = bgra(background)
    pv = bgra(preview)
    if len(title.encode("utf-8")) > 58:
        raise ValueError("title too long")

    template = TEMPLATE.read_bytes()
    if len(template) != HEADER_SIZE + FACE_SIZE:
        raise ValueError("header_template.bin must be 256 bytes")

    header = bytearray(template[:HEADER_SIZE])
    face = bytearray(template[HEADER_SIZE:])

    # global header
    struct.pack_into("<I", header, 28, 1)          # face count
    header[40:56] = face_id.encode("ascii")[:15].ljust(16, b"\0")
    header[104:104 + len(title.encode("utf-8"))] = title.encode("utf-8")

    # section offsets
    element_table = HEADER_SIZE + FACE_SIZE          # 256
    image_table = element_table + 16                 # 272
    element_data = image_table + 16                  # 288
    data_offset = element_data + 16                  # 304
    bg_block = image_block(bg)
    pv_offset = data_offset + len(bg_block)

    # face entry fields (as produced by DefaultPacker for one image, no widgets)
    fields = {
        0: 0,
        4: pv_offset,          # preview offset
        8: 1,                  # element count
        12: element_table,
        16: 0,                 # widget count
        20: image_table,
        24: 1,                 # image count
        28: image_table,
        32: 0,                 # imagelist count
        36: element_data,      # next section offset
        40: 0, 44: element_data,
        48: 0, 52: element_data,
        56: 0, 60: element_data,
        64: 0, 68: element_data,
        72: 0, 76: element_data,
        80: 0, 84: element_data,
    }
    for off, value in fields.items():
        struct.pack_into("<I", face, off, value)

    struct.pack_into("<I", header, 32, pv_offset)

    # element table: one background element at data_offset, size 16
    elem = struct.pack("<IIII", 0, 0, element_data, 16)
    # image table: index 0, type 2, offset, block length
    img = struct.pack("<IIII", 0x02000000, 0, data_offset, len(bg_block))

    out = bytearray()
    out += header
    out += face
    out += elem
    out += img
    out += b"\0\0\0\x02" + b"\0" * 12   # element record (type 2)
    out += bg_block
    out += image_block(pv)
    Path(out_path).write_bytes(out)
    print(f"wrote {out_path} ({len(out)} bytes, preview@{pv_offset})")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--background", required=True)
    ap.add_argument("--preview", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--title", default="Custom")
    ap.add_argument("--id", default="167210065")
    args = ap.parse_args()
    pack(args.background, args.preview, args.out, args.title, args.id)


if __name__ == "__main__":
    main()
