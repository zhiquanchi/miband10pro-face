#!/usr/bin/env python3
"""Drive the Band 10 Pro emulator: boot, tap, screenshot — all headless.

Touch reaches the guest through QMP `input-send-event` using the same
absolute-axis encoding as models/band10pro/tools/sensor_control.py.

  python3 tools/onboard_p67.py --boot-wait 150 \
      --step 简体中文=168,132 --step next=168,430 --shots 6

Each --step is "label=x,y"; a screendump is written before and after every tap
into --out so the whole flow is reviewable as PNGs.
"""
import argparse
import json
import socket
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
MODEL = HERE / "models/band10pro"
WIDTH, HEIGHT = 336, 480
DEFAULT_QEMU = HERE / ".build-tci/band10pro/build/qemu-system-arm"


class QMP:
    def __init__(self, port, retries=60):
        last = None
        for _ in range(retries):
            try:
                self.sock = socket.create_connection(("127.0.0.1", port), timeout=30)
                break
            except OSError as exc:
                last = exc
                time.sleep(0.5)
        else:
            raise last
        self.f = self.sock.makefile("rw", encoding="utf-8", newline="\n")
        self.buf = ""
        self._msg()

    def _msg(self):
        depth = 0
        while True:
            ch = self.f.read(1)
            if not ch:
                raise EOFError("QMP closed")
            self.buf += ch
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    obj, self.buf = json.loads(self.buf), ""
                    return obj

    def call(self, name, **args):
        self.f.write(json.dumps({"execute": name, "arguments": args}) + "\n")
        self.f.flush()
        while True:
            obj = self._msg()
            if "return" in obj or "error" in obj:
                return obj

    def close(self):
        try:
            self.call("quit")
        except Exception:
            pass
        self.sock.close()


def axis(value, size):
    return round(value * 32767 / (size - 1))


def move(qmp, x, y):
    qmp.call("input-send-event", events=[
        {"type": "abs", "data": {"axis": "x", "value": axis(x, WIDTH)}},
        {"type": "abs", "data": {"axis": "y", "value": axis(y, HEIGHT)}}])


def button(qmp, down):
    qmp.call("input-send-event", events=[
        {"type": "btn", "data": {"button": "left", "down": down}}])


def _send(qmp, events):
    r = qmp.call("input-send-event", events=events)
    if "error" in r:          # upstream QEMU names the member `e`
        r = qmp.call("input-send-event", e=events)
        if "error" in r:
            print("input-send-event failed:", r["error"])
            return False
    return True


def tap(qmp, x, y, hold=0.08):
    events = [
        {"type": "abs", "data": {"axis": "x", "value": axis(x, WIDTH)}},
        {"type": "abs", "data": {"axis": "y", "value": axis(y, HEIGHT)}},
    ]
    for down in (True, False):
        if not _send(qmp, events + [{"type": "btn", "data": {"button": "left", "down": down}}]):
            return False
        time.sleep(hold)
    return True


SWIPES = {  # models/band10pro/tools/sensor_control.py gesture() endpoints
    "up":    ((168, 390), (168, 90)),
    "down":  ((168, 90), (168, 390)),
    "left":  ((280, 240), (56, 240)),
    "right": ((56, 240), (280, 240)),
}


def swipe(qmp, name, steps=10, hold=0.05):
    (x0, y0), (x1, y1) = SWIPES[name]
    _send(qmp, [{"type": "abs", "data": {"axis": "x", "value": axis(x0, WIDTH)}},
                {"type": "abs", "data": {"axis": "y", "value": axis(y0, HEIGHT)}}])
    _send(qmp, [{"type": "btn", "data": {"button": "left", "down": True}}])
    time.sleep(hold)
    for i in range(1, steps + 1):
        x = x0 + (x1 - x0) * i / steps
        y = y0 + (y1 - y0) * i / steps
        _send(qmp, [{"type": "abs", "data": {"axis": "x", "value": axis(int(x), WIDTH)}},
                    {"type": "abs", "data": {"axis": "y", "value": axis(int(y), HEIGHT)}}])
        time.sleep(hold)
    _send(qmp, [{"type": "btn", "data": {"button": "left", "down": False}}])
    return True


def dump(qmp, out, name):
    ppm = out / f"{name}.ppm"
    r = qmp.call("screendump", filename=str(ppm))
    if "error" in r:
        print("screendump error:", r["error"])
        return None
    png = out / f"{name}.png"
    subprocess.run(["sips", "-s", "format", "png", str(ppm), "--out", str(png)],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return png if png.exists() else ppm


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=MODEL / "userdata/onboard")
    ap.add_argument("--qemu", default=str(DEFAULT_QEMU))
    ap.add_argument("--boot-wait", type=float, default=150.0)
    ap.add_argument("--step-wait", type=float, default=12.0)
    ap.add_argument("--step", action="append", default=[], metavar="LABEL=X,Y")
    ap.add_argument("--no-skip-binding", action="store_true",
                    help="keep the stock pairing flow (default injects diag-skip-binding)")
    args = ap.parse_args()

    qemu = Path(args.qemu)
    if not qemu.is_file():
        sys.exit(f"missing {qemu}")
    args.out.mkdir(parents=True, exist_ok=True)
    for stale in args.out.glob("*.ppm"):
        stale.unlink()

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()

    machine = (
        "bes1503,hardware-config=config/hardware.json,"
        "resource-image=firmware/vela_resource.bin,"
        "nor-fs-image=config/factory/nor-seed.bin,"
        "flash-state=userdata/flash.journal,"
        "gpu-scanout-bridge=off,display-rotation=0"
    )
    if not args.no_skip_binding:
        # best1502p.c / miband.c expose diag-skip-binding to bypass the
        # "download Mi Fitness to pair" screen, which needs a real phone.
        machine += ",diag-skip-binding=on"
    command = [str(qemu), "-M", machine, "-kernel", "firmware/vela_ap.bin",
               "-L", str(qemu.parents[2] / "qemu/pc-bios"),
               "-display", "none", "-monitor", "none",
               "-qmp", f"tcp:127.0.0.1:{port},server=on,wait=off"]
    log = (args.out / "qemu.log").open("w", encoding="utf8")
    proc = subprocess.Popen(command, cwd=MODEL, stdout=log, stderr=subprocess.STDOUT)
    print(f"qemu pid={proc.pid} qmp={port} (TCI boots are slow)")
    shots = []
    try:
        time.sleep(args.boot_wait)
        if proc.poll() is not None:
            print(f"qemu died rc={proc.returncode}")
            return 1
        qmp = QMP(port)
        qmp.call("qmp_capabilities")
        shots.append(dump(qmp, args.out, "000-boot"))
        for i, raw in enumerate(args.step, start=1):
            label, _, coords = raw.partition("=")
            if coords in SWIPES:
                print(f"swipe {i}: {label} {coords}")
                swipe(qmp, coords)
            else:
                try:
                    x, y = (int(v) for v in coords.split(","))
                except ValueError:
                    print(f"bad --step {raw!r} (want LABEL=x,y or LABEL=up|down|left|right)")
                    continue
                print(f"tap {i}: {label} @ ({x},{y})")
                tap(qmp, x, y)
            time.sleep(args.step_wait)
            shots.append(dump(qmp, args.out, f"{i:03d}-{label}"))
        qmp.close()
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        log.close()
    for p in shots:
        if p:
            print("wrote", p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
