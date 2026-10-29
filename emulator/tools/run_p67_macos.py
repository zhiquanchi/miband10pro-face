#!/usr/bin/env python3
"""Boot the Band 10 Pro firmware in the macOS QEMU build and dump the screen.

  python3 tools/run_p67_macos.py [--out DIR] [--wait 25] [--inject FILE.face]

Headless: QMP `screendump` writes a PPM we can convert and look at, so no SDL
window and no phone are needed to see what a watch face renders like.
"""
import argparse
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
MODEL = HERE / "models/band10pro"
# Default to the TCI build: the JIT build SIGILLs inside generated code on
# Apple Silicon (see macsim-BUILD-macOS.md). Pass --qemu to override.
DEFAULT_QEMU = HERE / ".build-tci/band10pro/build/qemu-system-arm"


class QMP:
    def __init__(self, port, timeout=20.0, connect_retries=40):
        last = None
        for _ in range(connect_retries):
            try:
                self.sock = socket.create_connection(("127.0.0.1", port), timeout=timeout)
                break
            except OSError as exc:
                last = exc
                time.sleep(0.5)
        else:
            raise last
        self.f = self.sock.makefile("rw", encoding="utf-8", newline="\n")
        self.buf = ""
        self._read()  # greeting

    def _read(self):
        while True:
            line = self.f.readline()
            if not line:
                raise EOFError("QMP closed")
            self.buf += line
            if self.buf.strip().startswith("{") and self._balanced():
                obj, self.buf = json.loads(self.buf), ""
                return obj

    def _balanced(self):
        depth = 0
        for ch in self.buf:
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
        return depth == 0 and "}" in self.buf

    def cmd(self, name, **args):
        self.f.write(json.dumps({"execute": name, "arguments": args}) + "\n")
        self.f.flush()
        while True:
            obj = self._read()
            if "return" in obj or "error" in obj:
                return obj
            # skip asynchronous events

    def close(self):
        try:
            self.cmd("quit")
        except Exception:
            pass
        self.sock.close()


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=MODEL / "userdata/screenshot-session")
    ap.add_argument("--wait", type=float, default=25.0, help="seconds to let the firmware boot")
    ap.add_argument("--headless", action="store_true", default=True)
    ap.add_argument("--display", default="none", help="'none' (QMP only) or 'sdl,gl=off'")
    ap.add_argument("--shots", type=int, default=1, help="how many screendumps, 3s apart")
    ap.add_argument("--qemu", default=str(DEFAULT_QEMU))
    args = ap.parse_args()

    qemu_bin = Path(args.qemu)
    if not qemu_bin.is_file():
        sys.exit(f"build QEMU first: {qemu_bin}")
    args.out.mkdir(parents=True, exist_ok=True)
    port = free_port()
    machine = (
        "bes1503,hardware-config=config/hardware.json,"
        "resource-image=firmware/vela_resource.bin,"
        "nor-fs-image=config/factory/nor-seed.bin,"
        "flash-state=userdata/flash.journal,"
        "gpu-scanout-bridge=off,display-rotation=0"
    )
    command = [str(qemu_bin), "-M", machine,
               "-kernel", "firmware/vela_ap.bin",
               "-L", str(qemu_bin.parents[2] / "qemu/pc-bios"),
               "-display", args.display, "-monitor", "none",
               "-qmp", f"tcp:127.0.0.1:{port},server=on,wait=off",
               "-d", "guest_errors", "-D", "userdata/qemu-guest-errors.log"]
    log = (args.out / "qemu.log").open("w", encoding="utf8")
    proc = subprocess.Popen(command, cwd=MODEL, stdout=log, stderr=subprocess.STDOUT)
    print(f"qemu pid={proc.pid} qmp port={port}")
    try:
        qmp = QMP(port)
        qmp.cmd("qmp_capabilities")
        time.sleep(args.wait)
        if proc.poll() is not None:
            print(f"qemu exited early rc={proc.returncode}; see {log.name}")
            return 1
        for i in range(max(1, args.shots)):
            ppm = args.out / f"screen-{i:02d}.ppm"
            r = qmp.cmd("screendump", filename=str(ppm))
            if "error" in r:
                print("screendump error:", r["error"])
                break
            time.sleep(3)
        info = qmp.cmd("query-status")
        print("status:", info.get("return"))
        qmp.close()
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                proc.kill()
        log.close()
    produced = sorted(args.out.glob("screen-*.ppm"))
    for p in produced:
        print(f"wrote {p} ({p.stat().st_size} bytes)")
    return 0 if produced else 1


if __name__ == "__main__":
    sys.exit(main())
