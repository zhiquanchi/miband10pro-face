#!/usr/bin/env python3
"""Build the custom Band 10 Pro face from the design assets, natively.

Layout can be overridden with --layout JSON:
{
  "title": "DataDash",
  "time":    {"x": 14, "y": 6,   "size": 94},
  "steps":   {"x": 29, "y": 372, "size": 40, "digits": 5},
  "battery": {"x": 47, "y": 18,  "size": 11, "digits": 3}
}
Coordinates are 1x cell top-left on a 336x480 canvas.
"""
import argparse
import json
import math
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from xwf_build import ImageElement, DigitWidget, ListWidget, build, W, H

ROOT = Path(__file__).resolve().parent.parent
AVENIR = "/System/Library/Fonts/Avenir Next Condensed.ttc"
PINGFANG = "/System/Library/Fonts/Hiragino Sans GB.ttc"

TIME_COLOR = (234, 244, 244, 255)
STEPS_COLOR = (234, 244, 244, 255)
BATTERY_COLOR = (159, 179, 182, 255)
MUTED = (111, 132, 137, 255)

DEFAULT_LAYOUT = {
    "title": "DataDash",
    "time": {"x": 14, "y": 6, "size": 94},
    "steps": {"x": 29, "y": 372, "size": 40, "digits": 5},
    "battery": {"x": 47, "y": 18, "size": 11, "digits": 3},
}


def merge(base, override):
    out = {k: (dict(v) if isinstance(v, dict) else v) for k, v in base.items()}
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k].update(v)
        else:
            out[k] = v
    return out


def font(size, path=AVENIR, index=0):
    return ImageFont.truetype(path, int(size), index=index)


def digit_metrics(f):
    cw = max(math.ceil(f.getlength(str(i))) for i in range(10)) + 1
    ascent, descent = f.getmetrics()
    return cw, ascent + descent, ascent


def gradient_fill(mask, top=(255, 255, 255), bottom=(201, 230, 224)):
    w, h = mask.size
    grad = Image.new("RGBA", (1, h))
    px = grad.load()
    for y in range(h):
        t = y / max(1, h - 1)
        px[0, y] = (
            round(top[0] + (bottom[0] - top[0]) * t),
            round(top[1] + (bottom[1] - top[1]) * t),
            round(top[2] + (bottom[2] - top[2]) * t),
            255,
        )
    grad = grad.resize((w, h))
    grad.putalpha(mask)
    return grad


def make_digit_set(outdir: Path, prefix: str, size: int, color, gradient=False):
    f = font(size)
    cw, ch, ascent = digit_metrics(f)
    paths = []
    for i in range(10):
        im = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        d.text((cw / 2, ascent), str(i), font=f, fill=color, anchor="ms")
        if gradient:
            im = gradient_fill(im.getchannel("A"))
        p = outdir / f"{prefix}{i}.png"
        im.save(p)
        paths.append(str(p))
    return paths, cw, ch


def make_glyph(outdir: Path, name: str, text: str, size: int, color, font_path=AVENIR,
               index=0, gradient=False):
    f = font(size, font_path, index)
    ascent, descent = f.getmetrics()
    cw = max(1, math.ceil(f.getlength(text)) + 1)
    ch = ascent + descent
    im = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.text((cw / 2, ascent), text, font=f, fill=color, anchor="ms")
    if gradient:
        im = gradient_fill(im.getchannel("A"))
    p = outdir / f"{name}.png"
    im.save(p)
    return str(p), cw, ch


def make_list_set(outdir: Path, prefix: str, labels, size: int, color,
                  font_path=AVENIR, index=0):
    f = font(size, font_path, index)
    ascent, descent = f.getmetrics()
    cw = max(math.ceil(f.getlength(s)) for s in labels) + 2
    ch = ascent + descent
    paths = []
    for i, s in enumerate(labels):
        im = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        d.text((cw / 2, ascent), s, font=f, fill=color, anchor="ms")
        p = outdir / f"{prefix}{i}.png"
        im.save(p)
        paths.append(str(p))
    return paths, cw, ch


def composite_mock(bg_path: Path, out: Path, time_paths, time_cw,
                   colon_path, steps_paths, steps_cw,
                   unit_path, batt_paths, batt_cw, pct_path,
                   time_pos, colon_pos, steps_pos, unit_pos, batt_pos, pct_pos,
                   steps_digits=4, batt_digits=2):
    im = Image.open(bg_path).convert("RGBA")

    def paste_digits(paths, cw, xy, chars):
        x, y = xy
        for ch_ in chars:
            p = paths[int(ch_)]
            im.alpha_composite(Image.open(p).convert("RGBA"), (int(x), int(y)))
            x += cw

    paste_digits(time_paths, time_cw, time_pos, "15")
    im.alpha_composite(Image.open(colon_path).convert("RGBA"), colon_pos)
    paste_digits(time_paths, time_cw,
                 (colon_pos[0] + Image.open(colon_path).width + 4, time_pos[1]), "54")
    paste_digits(steps_paths, steps_cw, steps_pos, "8432"[:steps_digits])
    im.alpha_composite(Image.open(unit_path).convert("RGBA"), unit_pos)
    paste_digits(batt_paths, batt_cw, batt_pos, "76"[:batt_digits])
    im.alpha_composite(Image.open(pct_path).convert("RGBA"), pct_pos)
    im.save(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--background", default=str(ROOT / "native" / "assets" / "background.png"))
    ap.add_argument("--preview", default=str(ROOT / "native" / "assets" / "preview.png"))
    ap.add_argument("--out", default=str(ROOT / "native" / "design.face"))
    ap.add_argument("--mock", default=str(ROOT / "native" / "design_mock.png"))
    ap.add_argument("--title", default="")
    ap.add_argument("--layout", default="")
    args = ap.parse_args()

    layout = DEFAULT_LAYOUT
    if args.layout and Path(args.layout).exists():
        layout = merge(DEFAULT_LAYOUT, json.loads(Path(args.layout).read_text(encoding="utf-8")))
    title = args.title or layout.get("title") or "DataDash"

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.mock).parent.mkdir(parents=True, exist_ok=True)

    t_size = int(layout["time"]["size"])
    s_size = int(layout["steps"]["size"])
    b_size = int(layout["battery"]["size"])
    s_digits = int(layout["steps"].get("digits", 5))
    b_digits = int(layout["battery"].get("digits", 3))

    tmp = Path(tempfile.mkdtemp(prefix="xwf_"))
    time_paths, t_cw, t_ch = make_digit_set(tmp, "t", t_size, TIME_COLOR, gradient=True)
    steps_paths, s_cw, s_ch = make_digit_set(tmp, "s", s_size, STEPS_COLOR)
    batt_paths, b_cw, b_ch = make_digit_set(tmp, "b", b_size, BATTERY_COLOR)
    temp_paths, p_cw, p_ch = make_digit_set(tmp, "p", 47, TIME_COLOR)
    date_paths, d_cw, d_ch = make_digit_set(tmp, "d", 14, TIME_COLOR)

    colon, colon_w, colon_h = make_glyph(tmp, "colon", ":", t_size, TIME_COLOR, gradient=True)
    unit, unit_w, unit_h = make_glyph(tmp, "unit", "步", 9, MUTED, PINGFANG, 0)
    pct, pct_w, pct_h = make_glyph(tmp, "pct", "%", b_size, BATTERY_COLOR)
    deg, deg_w, deg_h = make_glyph(tmp, "deg", "°", 26, (111, 216, 195, 255))
    yue, yue_w, yue_h = make_glyph(tmp, "yue", "月", 14, TIME_COLOR, PINGFANG, 0)
    ri, ri_w, ri_h = make_glyph(tmp, "ri", "日", 14, TIME_COLOR, PINGFANG, 0)

    cn_paths, cn_w, cn_h = make_list_set(
        tmp, "cn", ["周日", "周一", "周二", "周三", "周四", "周五", "周六"],
        21, TIME_COLOR, PINGFANG, 0)
    en_paths, en_w, en_h = make_list_set(
        tmp, "en", ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"],
        9, MUTED, AVENIR, 0)
    cn_x, cn_y = 320 - cn_w, 63
    en_x, en_y = 320 - en_w, 52
    temp_x, temp_y = 84, 170
    deg_x = temp_x + 2 * p_cw + 2
    deg_y = temp_y + 6
    date_x, date_y = 16, 134
    yue_x = date_x + 2 * d_cw + 1
    day_x = yue_x + yue_w + 1
    ri_x = day_x + 2 * d_cw + 1

    time_x = round(layout["time"]["x"])
    time_y = round(layout["time"]["y"])
    colon_x = time_x + 2 * t_cw + 2
    minute_x = colon_x + colon_w + 4

    steps_x = round(layout["steps"]["x"])
    steps_y = round(layout["steps"]["y"])
    unit_x = steps_x + s_digits * s_cw + 2
    unit_y = steps_y + font(s_size).getmetrics()[0] + 9 - font(9, PINGFANG).getmetrics()[0]

    batt_x = round(layout["battery"]["x"])
    batt_y = round(layout["battery"]["y"])
    pct_x = batt_x + b_digits * b_cw + 1

    extra = [
        ImageElement("colon", colon, colon_x, time_y),
        ImageElement("unit", unit, unit_x, unit_y),
        ImageElement("pct", pct, pct_x, batt_y),
        ImageElement("deg", deg, deg_x, deg_y),
        ImageElement("yue", yue, yue_x, date_y),
        ImageElement("ri", ri, ri_x, date_y),
    ]
    digits = [
        DigitWidget("hour", time_paths, time_x, time_y, 0x0811, 2, 0, 0),
        DigitWidget("minute", time_paths, minute_x, time_y, 0x1011, 2, 0, 0),
        DigitWidget("steps", steps_paths, steps_x, steps_y, 0x0821, s_digits, 0, 0),
        DigitWidget("battery", batt_paths, batt_x, batt_y, 0x0841, b_digits, 0, 0),
        DigitWidget("temp", temp_paths, temp_x, temp_y, 0x2031, 2, 0, 0),
        DigitWidget("month", date_paths, date_x, date_y, 0x1012, 2, 0, 0),
        DigitWidget("day", date_paths, day_x, date_y, 0x1812, 2, 0, 0),
        ListWidget("weekday_cn", cn_paths, cn_x, cn_y, 0x2012, list(range(7))),
        ListWidget("weekday_en", en_paths, en_x, en_y, 0x2012, list(range(7))),
    ]

    out = Path(args.out)
    build(ImageElement("bg", args.background), args.preview, digits, str(out),
          title=title, extra_images=extra)
    mock = Path(args.mock)
    composite_mock(Path(args.background), mock, time_paths, t_cw,
                   colon, steps_paths, s_cw, unit, batt_paths, b_cw, pct,
                   (time_x, time_y), (colon_x, time_y), (steps_x, steps_y),
                   (unit_x, unit_y), (batt_x, batt_y), (pct_x, batt_y),
                   s_digits, b_digits)
    m = Image.open(mock).convert("RGBA")
    x = temp_x
    for ch in "21":
        m.alpha_composite(Image.open(temp_paths[int(ch)]).convert("RGBA"), (x, temp_y))
        x += p_cw
    m.alpha_composite(Image.open(deg).convert("RGBA"), (deg_x, deg_y))
    x = date_x
    for ch in "10":
        m.alpha_composite(Image.open(date_paths[int(ch)]).convert("RGBA"), (x, date_y))
        x += d_cw
    m.alpha_composite(Image.open(yue).convert("RGBA"), (yue_x, date_y))
    x = day_x
    for ch in "28":
        m.alpha_composite(Image.open(date_paths[int(ch)]).convert("RGBA"), (x, date_y))
        x += d_cw
    m.alpha_composite(Image.open(ri).convert("RGBA"), (ri_x, date_y))
    m.alpha_composite(Image.open(cn_paths[3]).convert("RGBA"), (cn_x, cn_y))
    m.alpha_composite(Image.open(en_paths[3]).convert("RGBA"), (en_x, en_y))
    m.save(mock)
    print("digits dir", tmp)
    print("time", time_x, time_y, t_size, "cell", t_cw, t_ch)
    print("steps", steps_x, steps_y, s_size, "cell", s_cw, s_ch)
    print("battery", batt_x, batt_y, b_size, "cell", b_cw, b_ch)
    print("colon", colon_w, colon_h, "unit", unit_w, unit_h, "pct", pct_w, pct_h)
    print("minute_x", minute_x, "pct_x", pct_x, "unit_x", unit_x)
    print("weekday", cn_x, cn_y, cn_w, cn_h, "|", en_x, en_y, en_w, en_h)
    print("temp", temp_x, temp_y, "cell", p_cw, p_ch, "deg", deg_x, deg_y)
    print("date", date_x, date_y, "cell", d_cw, d_ch, "yue", yue_x, "day", day_x, "ri", ri_x)
    print("mock", mock)


if __name__ == "__main__":
    main()
