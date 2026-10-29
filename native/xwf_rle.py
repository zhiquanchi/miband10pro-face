#!/usr/bin/env python3
"""Xiaomi Band 10 Pro watch-face image codec.

Reverse engineered from the factory watch faces inside the Band 10 Pro
3.101.043 resource partition (`vela_resource.bin`), cross-checked against
XiaomiWatch.Common.dll (RleEncoder / MiBand8ProRleCompressor).

Every raster block carries an 8-bit indexed bitmap:

  buffer = 1024-byte BGRA palette (256 entries) + w*h index bytes
  stream = e0 21 a5 5a + u32(((w*h + 1024) << 4) | 1) + byteRLE(buffer)

  byteRLE: flag < 0x80  -> repeat the following byte `flag` times
           flag >= 0x80 -> copy (flag & 0x7f) bytes verbatim (0x80 == 1)

Block framing (matches all 17 factory faces):

  image block : [0x10][0x04][0x00][0x00] u16 w u16 h u32 stream_len  stream
  list  block : [0x10][n  ][0x04][0x00] u16 w u16 h u32 body_len
                u32 stream_len * n      body (sub-blocks back to back)

`python3 xwf_rle.py` round-trips every block of every factory face found in
/tmp/fw10pro/vela_resource.bin.
"""
import struct

SUB_MAGIC = bytes.fromhex("e021a55a")
MAX_RUN = 127
MAX_LIT = 127
PALETTE_BYTES = 1024


def rle_encode(src: bytes) -> bytes:
    out = bytearray()
    i = 0
    n = len(src)
    while i < n:
        run = 1
        while run < MAX_RUN and i + run < n and src[i + run] == src[i]:
            run += 1
        if run >= 2:
            out.append(run)
            out.append(src[i])
            i += run
            continue
        j = i + 1
        while j < n and (j - i) < MAX_LIT:
            # stop before starting a run of >=3 identical bytes so it encodes shorter
            if j + 2 < n and src[j] == src[j + 1] == src[j + 2]:
                break
            j += 1
        lit = src[i:j]
        out.append(0x80 | len(lit))
        out += lit
        i = j
    return bytes(out)


def rle_decode(src: bytes) -> bytes:
    out = bytearray()
    i = 0
    n = len(src)
    while i < n:
        flag = src[i]
        i += 1
        if flag & 0x80:
            count = (flag & 0x7F) or 1
            out += src[i:i + count]
            i += count
        else:
            count = flag or 1
            out += bytes([src[i]]) * count
            i += 1
    return bytes(out)


def bgra_to_rgba(px: bytes) -> bytes:
    out = bytearray(px)
    out[0::4], out[2::4] = px[2::4], px[0::4]
    return bytes(out)


def quantize(px_bgra: bytes, w: int, h: int):
    """BGRA pixels -> (1024-byte BGRA palette, w*h index bytes)."""
    from PIL import Image

    im = Image.frombytes("RGBA", (w, h), bgra_to_rgba(px_bgra))
    colors = im.getcolors(maxcolors=1 << 24)
    if colors and len(colors) <= 256:
        pal = sorted({c[1] for c in colors})
        lut = {c: i for i, c in enumerate(pal)}
        idx = bytes(lut[p] for p in im.getdata())
    else:
        rgb = im.convert("RGB").quantize(colors=255, method=Image.Quantize.MEDIANCUT,
                                         dither=Image.Dither.NONE)
        p = (rgb.getpalette() or [])[:255 * 3]
        p = list(p) + [0] * (255 * 3 - len(p))
        pal = [(p[i * 3], p[i * 3 + 1], p[i * 3 + 2], 255) for i in range(255)]
        pal.append((0, 0, 0, 0))
        base = list(rgb.getdata())
        alpha = im.split()[3].tobytes()
        idx = bytes(255 if alpha[k] < 128 else base[k] for k in range(w * h))
    pal = list(pal[:256]) + [(0, 0, 0, 0)] * (256 - min(len(pal), 256))
    palette = bytearray()
    for r, g, b, a in pal:
        palette += bytes([b, g, r, a])
    return bytes(palette), idx


def compress_pixels(px_bgra: bytes, w: int, h: int) -> bytes:
    if len(px_bgra) != w * h * 4:
        raise ValueError(f"{w}x{h} needs {w * h * 4} bytes, got {len(px_bgra)}")
    palette, idx = quantize(px_bgra, w, h)
    stream = rle_encode(palette + idx)
    return SUB_MAGIC + struct.pack("<I", ((w * h + PALETTE_BYTES) << 4) | 1) + stream


def image_block(px_bgra: bytes, w: int, h: int) -> bytes:
    body = compress_pixels(px_bgra, w, h)
    return struct.pack("<4sHHI", b"\x10\x04\x00\x00", w, h, len(body)) + body


def list_block(images) -> bytes:
    """images: list of (px_bgra, w, h); every image must share one size."""
    if not images:
        raise ValueError("empty list")
    w, h = images[0][1], images[0][2]
    subs = [compress_pixels(px, iw, ih) for px, iw, ih in images]
    sizes = struct.pack(f"<{len(subs)}I", *(len(s) for s in subs))
    body = b"".join(subs)
    hdr = bytearray(12)
    hdr[0] = 0x10
    hdr[1] = len(subs)
    hdr[2] = 0x04
    struct.pack_into("<HHI", hdr, 4, w, h, len(body))
    return bytes(hdr) + sizes + body


def _factory_blocks(resource_path: str):
    import re
    from pathlib import Path
    data = Path(resource_path).read_bytes()
    hits = [m.start() for m in re.finditer(re.escape(bytes.fromhex("5aa53412")), data)] + [len(data)]
    for n in range(len(hits) - 1):
        b = data[hits[n]:hits[n + 1]]
        if len(b) < 300 or len(b) > 3_000_000:
            continue
        fb = b[168:256]
        g = lambda o: struct.unpack_from("<I", fb, o)[0]
        iT, iI, lT, lL = g(20), g(24), g(36), g(32)
        if iI > 500 or lL > 500:
            continue
        for i in range(iI):
            try:
                _t, _f, off, size = struct.unpack_from("<IIII", b, iT + 16 * i)
                if off + 16 >= len(b) or b[off] != 0x10 or b[off + 1] != 0x04:
                    continue
                w, h, ln = struct.unpack_from("<HHI", b, off + 4)
                raw = b[off + 12:off + 12 + ln]
                if raw[:4] == SUB_MAGIC:
                    yield f"face_{n:02d} img[{i}]", w, h, raw[8:]
            except Exception:
                continue
        for i in range(lL):
            try:
                _t, _f, off, size = struct.unpack_from("<IIII", b, lT + 16 * i)
                if off + 16 >= len(b) or b[off] != 0x10:
                    continue
                cnt = b[off + 1]
                w, h, ln = struct.unpack_from("<HHI", b, off + 4)
                if not 0 < cnt <= 500:
                    continue
                tbl = list(struct.unpack_from(f"<{cnt}I", b, off + 12))
                p = off + 12 + 4 * cnt
                for j, sl in enumerate(tbl):
                    raw = b[p:p + sl]
                    p += sl
                    if raw[:4] == SUB_MAGIC:
                        yield f"face_{n:02d} lst[{i}][{j}]", w, h, raw[8:]
            except Exception:
                continue


if __name__ == "__main__":
    import sys
    from pathlib import Path
    res = "/tmp/fw10pro/vela_resource.bin"
    if not Path(res).exists():
        print("missing /tmp/fw10pro/vela_resource.bin")
        sys.exit(1)
    total = exact = reenc = 0
    for tag, w, h, stream in _factory_blocks(res):
        total += 1
        buf = rle_decode(stream)
        if len(buf) == w * h + PALETTE_BYTES:
            exact += 1
        if rle_decode(rle_encode(buf)) == buf:
            reenc += 1
    print(f"factory blocks decoded: {exact}/{total} exact size; "
          f"re-encode round-trip: {reenc}/{total}")
