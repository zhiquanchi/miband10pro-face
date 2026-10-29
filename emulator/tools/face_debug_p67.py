#!/usr/bin/env python3
"""Emulator debug loop for Band 10 Pro watch faces — no phone, no radio.

Boots the stock 3.101.043 firmware in the macOS QEMU build with the *synthetic*
Bluetooth backend (the built-in virtual phone), installs a .face through the
same catalog commit path the real Bluetooth receiver uses, reads the catalog
back, and dumps the panel over QMP.

  python3 tools/face_debug_p67.py --face ../miband10pro-face/native/probe.face
  python3 tools/face_debug_p67.py --face .../design.face --boot-wait 150 --out ...

QOM surface used (source/overlays/band10pro/hw/arm/miband.c):
  install-file   write-only "<mass type>:<absolute path>", 16 = watchface
  install-catalog read-only committed catalog after the install
  bt-backend     defaults to "synthetic" when unset
"""
import argparse
import hashlib
import json
import socket
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
MODEL = HERE / "models/band10pro"
QEMU = HERE / ".build-tci/band10pro/build/qemu-system-arm"
WIDTH, HEIGHT = 336, 480
# start(x,y) -> end(x,y) for the four panel gestures
GESTURES = {"up": (168, 400, 168, 80), "down": (168, 80, 168, 400),
            "left": (280, 240, 60, 240), "right": (60, 240, 280, 240)}
# HCI LE Connection Complete, Role=peripheral(0x01), peer 02:00:00:10:00:01,
# interval 30 ms, latency 0, timeout 4 s, master features from the BES driver.
# Verified reproducible in gatt5: the band answers on the ATT layer. Only works
# from the factory-fresh unbound state, i.e. together with --fresh-journal.
PHONE_LINK_EVENT = ("043e1a010000000100010000100002180000009001bfee4dfedbff7b87")


# ---------------------------------------------------------------------------
# Virtual-phone Xiaomi protocol, host side.
#
# The emulator models the *band* half of the MiBand BLE protocol
# (source/overlays/band10pro/hw/arm/miband_xiaomi_*.c): an ATT write to
# characteristic handle 6 carries an L1 frame, and a completed MASS transfer is
# handed to miband_install_catalog_commit() -- the very function a real phone
# install ends in.  So a phone session can be replayed from the host by
# encoding those frames here and pushing them through bt-synthetic-inject.
# ---------------------------------------------------------------------------
L1_ACK, L1_CMD, L1_DATA = 1, 2, 3
ATT_WRITE_REQUEST = 0x12
ATT_CHAR_HANDLE = 6          # the tx characteristic the band accepts writes on
HCI_ACL_HANDLE = 0x0000      # matches PHONE_LINK_EVENT's connection handle
MASS_WATCHFACE = 16
MASS_SLICE = 1024            # leaves room inside the 2048-byte inject boundary


def crc16_arc(data):
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc & 0xFFFF


def l1_encode(l1_type, payload, sequence):
    body = bytes([0xA5, 0xA5, l1_type, sequence & 0xFF])
    body += len(payload).to_bytes(2, "little")
    body += crc16_arc(payload).to_bytes(2, "little")
    return body + payload


def l2_encode(channel, opcode, payload):
    return bytes([channel, opcode]) + payload


def pb_varint(value):
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        out.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(out)


def pb_field(number, payload):
    """Length-delimited field (wire type 2)."""
    return bytes([(number << 3) | 2]) + pb_varint(len(payload)) + payload


def pb_scalar(number, value):
    """Varint field (wire type 0)."""
    return bytes([(number << 3) | 0]) + pb_varint(value)


def acl_frame(att_pdu):
    """H4 ACL -> L2CAP (CID 0x0004 = ATT) -> ATT PDU.  Both length fields cover
    everything after themselves, matching the band's own indications."""
    l2cap = len(att_pdu).to_bytes(2, "little") + b"\x04\x00" + att_pdu
    acl = len(l2cap).to_bytes(2, "little")
    return (b"\x02" + HCI_ACL_HANDLE.to_bytes(2, "little") + acl + l2cap).hex()


def att_write(payload):
    return bytes([ATT_WRITE_REQUEST]) + ATT_CHAR_HANDLE.to_bytes(2, "little") + payload


def prepare_watchface_request(face_id, total_size, sequence):
    """WatchfaceInfo{1: id string, 2: size} inside Request{1: 4, 2: 4, 6: info}."""
    info = pb_field(1, face_id.encode()) + pb_scalar(2, total_size)
    pb = pb_scalar(1, 4) + pb_scalar(2, 4) + pb_field(6, info)
    return acl_frame(att_write(l1_encode(L1_DATA, l2_encode(1, 1, pb), sequence)))


def mass_chunk_frames(face_bytes, face_id, sequence_start, mass_type=MASS_WATCHFACE):
    """One MASS packet -> numbered slices, exactly as the Mi Fitness app sends them."""
    import zlib
    md5 = hashlib.md5(face_bytes).digest()
    mass = (bytes([0, mass_type]) + md5 + len(face_bytes).to_bytes(4, "little")
            + face_bytes)
    mass += zlib.crc32(mass).to_bytes(4, "little")
    parts = (len(mass) + MASS_SLICE - 1) // MASS_SLICE
    seq = sequence_start
    for index in range(parts):
        body = (parts.to_bytes(2, "little") + (index + 1).to_bytes(2, "little")
                + mass[index * MASS_SLICE:(index + 1) * MASS_SLICE])
        yield acl_frame(att_write(l1_encode(L1_DATA, l2_encode(2, 1, body), seq)))
        seq += 1


class QMP:
    def __init__(self, port, retries=80):
        last = None
        for _ in range(retries):
            try:
                self.sock = socket.create_connection(("127.0.0.1", port), timeout=60)
                break
            except OSError as exc:
                last = exc
                time.sleep(0.5)
        else:
            raise last
        self.f = self.sock.makefile("rw", encoding="utf-8", newline="\n")
        self.pending = ""

    def _msg(self):
        depth = 0
        while True:
            ch = self.f.read(1)
            if not ch:
                raise EOFError("QMP closed")
            self.pending += ch
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    obj, self.pending = json.loads(self.pending), ""
                    return obj

    def call(self, name, **args):
        self.f.write(json.dumps({"execute": name, "arguments": args}) + "\n")
        self.f.flush()
        while True:
            obj = self._msg()
            if "return" in obj or "error" in obj:
                return obj

    def qom_get(self, prop, path="/machine"):
        r = self.call("qom-get", path=path, property=prop)
        if "error" in r:
            return {"__error": r["error"]["desc"]}
        return r["return"]

    def qom_set(self, prop, value, path="/machine"):
        r = self.call("qom-set", path=path, property=prop, value=value)
        return "return" in r, r.get("error", {}).get("desc", "")

    def close(self):
        try:
            self.call("quit")
        except Exception:
            pass
        self.sock.close()


def axis(value, size):
    return round(value * 32767 / (size - 1))


def tap(qmp, x, y, hold=0.08):
    """Drive the guest touchscreen through the synthetic controller."""
    move = [{"type": "abs", "data": {"axis": "x", "value": axis(x, WIDTH)}},
            {"type": "abs", "data": {"axis": "y", "value": axis(y, HEIGHT)}}]
    for down in (True, False):
        r = qmp.call("input-send-event", events=move + [
            {"type": "btn", "data": {"button": "left", "down": down}}])
        if "error" in r:
            print(f"  tap failed: {r['error']['desc']}")
            return False
        time.sleep(hold)
    return True


def swipe(qmp, x0, y0, x1, y1, steps=8, hold=0.05):
    """Drag across the synthetic touch panel — page turns and 上滑 need motion."""
    def at(x, y, down=None):
        events = [{"type": "abs", "data": {"axis": "x", "value": axis(x, WIDTH)}},
                  {"type": "abs", "data": {"axis": "y", "value": axis(y, HEIGHT)}}]
        if down is not None:
            events.append({"type": "btn", "data": {"button": "left", "down": down}})
        qmp.call("input-send-event", events=events)
    at(x0, y0, True)
    for i in range(1, steps + 1):
        at(x0 + (x1 - x0) * i / steps, y0 + (y1 - y0) * i / steps)
        time.sleep(hold)
    at(x1, y1, False)
    return True


def dump(qmp, out, name):
    # QEMU's cwd is the model dir, so screendump needs an absolute path
    ppm = (out / f"{name}.ppm").resolve()
    r = qmp.call("screendump", filename=str(ppm))
    if "error" in r:
        print(f"  screendump failed: {r['error']['desc']}")
        return None
    png = out / f"{name}.png"
    subprocess.run(["sips", "-s", "format", "png", str(ppm), "--out", str(png)],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return png if png.exists() else ppm


def run_phone_session(qmp, face_path, out, report):
    """Replay what Mi Fitness does over BLE: connect, pb prepare, MASS chunks.

    Everything here runs against the emulator's band-side protocol model, so a
    successful pass means the virtual phone session is real -- not that the
    guest UI consumed the face (that still needs the /data write path).
    """
    payload = face_path.read_bytes()
    face_id = payload[40:64].split(b"\x00")[0].decode("ascii", "replace")
    report["phone_session"] = {"face": str(face_path), "id": face_id,
                               "bytes": len(payload)}
    print(f"phone session: install {face_id or '?'} ({len(payload):,} B) over the link")
    ok, err = qmp.qom_set("bt-synthetic-inject", PHONE_LINK_EVENT)
    print(f"  le connection complete -> {'ok' if ok else 'FAIL ' + err}")
    report["phone_session"]["link"] = ok
    time.sleep(2)
    ok, err = qmp.qom_set("bt-synthetic-inject",
                          prepare_watchface_request(face_id, len(payload), 1))
    print(f"  pb prepare watchface -> {'ok' if ok else 'FAIL ' + err}")
    report["phone_session"]["prepare"] = {"ok": ok, "error": err}
    time.sleep(2)
    report["phone_session"]["after_prepare"] = str(
        qmp.qom_get("bt-synthetic-last-bt-packet"))[:200]
    print("  band replied:", report["phone_session"]["after_prepare"])
    frames = list(mass_chunk_frames(payload, face_id, 2))
    failed = 0
    for index, hexs in enumerate(frames, start=1):
        ok, err = qmp.qom_set("bt-synthetic-inject", hexs)
        if not ok:
            failed += 1
            if failed == 1:
                print(f"  chunk {index}/{len(frames)} failed first: {err[:120]}")
        if index % 50 == 0:
            print(f"  mass {index}/{len(frames)}")
    report["phone_session"]["mass"] = {"frames": len(frames), "failed": failed}
    print(f"  mass chunks sent: {len(frames)}, failed: {failed}")
    time.sleep(4)
    catalog = str(qmp.qom_get("install-catalog"))
    report["phone_session"]["catalog_after"] = catalog[:600]
    report["phone_session"]["last_bt_packet"] = str(
        qmp.qom_get("bt-synthetic-last-bt-packet"))[:200]
    print("  catalog:", catalog[:400])
    print("  last band packet:", report["phone_session"]["last_bt_packet"])
    return catalog


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--face", type=Path, help="absolute .face to install (MASS type 16)")
    ap.add_argument("--out", type=Path, default=MODEL / "userdata/facedebug")
    ap.add_argument("--boot-wait", type=float, default=150.0)
    ap.add_argument("--settle", type=float, default=25.0, help="wait after install")
    ap.add_argument("--extra-wait", type=float, default=25.0, help="wait before final dump")
    ap.add_argument("--machine-extra", default="", help="extra -M key=value, e.g. diag-skip-binding=on")
    ap.add_argument("--resource", type=Path,
                    help="override firmware/vela_resource.bin (e.g. a copy with a face slot swapped)")
    ap.add_argument("--fresh-journal", action="store_true",
                    help="drop userdata/flash.journal first; required whenever the base image "
                         "changes, else the store rejects it with 'wrong base image or state header'")
    ap.add_argument("--tap", action="append", default=[], metavar="X,Y",
                    help="tap before each dump, e.g. 168,132 to pick a language in the wizard")
    ap.add_argument("--install-early", action="store_true",
                    help="commit the face as soon as QMP answers, before the firmware has "
                         "enumerated /data/app/watchface/market during boot")
    ap.add_argument("--reset-after-install", action="store_true",
                    help="system_reset the guest once the install lands, so the firmware "
                         "re-scans its watchface list from scratch on the next boot")
    ap.add_argument("--btpeek-late", action="store_true",
                    help="re-read the synthetic peer observability props *after* the "
                         "--inject packets, to see whether the guest answered at all")
    ap.add_argument("--phone-link", action="store_true",
                    help="shorthand for the known-good virtual-phone injection: an LE "
                         "Connection Complete with Role=peripheral. Requires --fresh-journal "
                         "(the factory-fresh unbound state is the only one that advertises)")
    ap.add_argument("--phone-session", type=Path, metavar="FACE",
                    help="replay a complete phone install over the synthetic BLE link: "
                         "LE connection, pb prepare, then the whole .face as MASS "
                         "chunks, exactly the frames Mi Fitness sends")
    ap.add_argument("--inject", action="append", default=[], metavar="HEX",
                    help="qom-set bt-synthetic-inject before the post-taps: one H4-framed "
                         "controller->host packet, optionally prefixed '<channel>:'. "
                         "e.g. 043e1a0100... is an LE Connection Complete, which the "
                         "emulator also hooks up to its synthetic GATT server")
    ap.add_argument("--post-gap", type=float, default=4.0,
                    help="seconds between --post-tap gestures; keep it under the "
                         "panel's ~10 s inactivity timeout to stay awake")
    ap.add_argument("--post-tap", action="append", default=[], metavar="ACTION",
                    help="same syntax as --tap but run after the install, e.g. "
                         "'lp:168,240,1.2' to long-press the watchface into the picker")
    ap.add_argument("--walk", type=int, default=0, metavar="N",
                    help="swing the accelerometer N times before every burst frame to feed "
                         "the pedometer, and set a heart rate -- this is how you get the "
                         "steps/HR data sources off zero without a phone on the other end")
    ap.add_argument("--bpm", type=float, default=0.0,
                    help="sensor-pulse-bpm to assert alongside --walk")
    ap.add_argument("--burst-tap", default="", metavar="X,Y",
                    help="tap point used between burst frames (defaults to panel centre)")
    ap.add_argument("--poke", action="append", default=[], metavar="PROP=VALUE",
                    help="qom-set before every burst frame, e.g. sensor-accel-y=9.8 "
                         "to simulate a wrist raise (raise-to-wake is the only wake "
                         "source once the panel sleeps outside the wizard)")
    ap.add_argument("--burst-gesture", choices=("up", "down", "left", "right"),
                    help="swipe instead of tap between burst frames (page turns, 上滑)")
    ap.add_argument("--burst-dbl", action="store_true",
                    help="double-tap between burst frames — the panel sleeps after ~10 s "
                         "outside the wizard and touch_sleep/double-tap is what wakes it")
    ap.add_argument("--burst", metavar="N:GAP",
                    help="take N screenshots GAP seconds apart (tapping first) to watch the "
                         "panel come up instead of guessing at one late frame")
    ap.add_argument("--memdump", action="append", default=[], metavar="ADDR:SIZE",
                    help="pmemsave a guest physical window to <out>/mem-<addr>.bin")
    ap.add_argument("--ap", type=Path, help="override firmware/vela_ap.bin (patched variant)")
    ap.add_argument("--qemu-extra", default="",
                    help="raw extra argv for qemu-system-arm, e.g. '-d unimp -D /tmp/x.log'")
    ap.add_argument("--btpeek", action="store_true",
                    help="read the synthetic peer observability props and drain AP packets")
    args = ap.parse_args()

    if not QEMU.is_file():
        sys.exit(f"missing TCI build: {QEMU}")
    face = args.face.resolve() if args.face else None
    if face and not face.is_file():
        sys.exit(f"face not found: {face}")
    args.out.mkdir(parents=True, exist_ok=True)
    for stale in args.out.glob("*.ppm"):
        stale.unlink()
    for stale in args.out.glob("*.png"):
        stale.unlink()
    ap = args.ap or (MODEL / "firmware/vela_ap.bin")
    args.ap = ap.resolve()
    # The flash store refuses a journal whose base image no longer matches
    # ("wrong base image or state header, errno=22"), and *both* the AP and the
    # /resource romfs count as part of that base. Track what we last booted
    # with and drop the journal automatically whenever either one changes.
    stamp = MODEL / "userdata/flash.journal.apsha"
    journal = MODEL / "userdata/flash.journal"
    digest = hashlib.sha256()
    digest.update(args.ap.read_bytes())
    if args.resource:
        digest.update(args.resource.read_bytes())
    ap_sha = digest.hexdigest()
    previous = stamp.read_text(encoding="utf8").strip() if stamp.exists() else ""
    if args.fresh_journal or (journal.exists() and previous and previous != ap_sha):
        for junk in (journal, journal.with_suffix(".journal.lock"), stamp):
            junk.unlink(missing_ok=True)
        print(f"dropped flash.journal (base {previous[:12] or 'unknown'} -> {ap_sha[:12]})")
    stamp.write_text(ap_sha + "\n", encoding="utf8")

    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    resource = str(args.resource.resolve()) if args.resource else "firmware/vela_resource.bin"
    machine = ("bes1503,hardware-config=config/hardware.json,"
               f"resource-image={resource},"
               "nor-fs-image=config/factory/nor-seed.bin,"
               "flash-state=userdata/flash.journal,"
               "gpu-scanout-bridge=off,display-rotation=0")
    if args.machine_extra:
        machine += "," + args.machine_extra
    command = [str(QEMU), "-M", machine, "-kernel", str(args.ap.resolve()) if args.ap else "firmware/vela_ap.bin",
               "-L", str(QEMU.parents[2] / "qemu/pc-bios"),
               "-display", "none", "-monitor", "none",
               "-qmp", f"tcp:127.0.0.1:{port},server=on,wait=off"]
    if args.qemu_extra:
        import shlex
        command += shlex.split(args.qemu_extra)
    log = (args.out / "qemu.log").open("w", encoding="utf8")
    proc = subprocess.Popen(command, cwd=MODEL, stdout=log, stderr=subprocess.STDOUT)
    print(f"qemu pid={proc.pid} qmp={port}")
    report = {"qemu_pid": proc.pid, "machine": machine}
    try:
        time.sleep(args.boot_wait)
        if proc.poll() is not None:
            print(f"qemu exited rc={proc.returncode}")
            report["error"] = f"qemu exited rc={proc.returncode}"
            return 1
        qmp = QMP(port)
        qmp.call("qmp_capabilities")
        report["bt_backend"] = qmp.qom_get("bt-backend")
        print(f"bt-backend = {report['bt_backend']}")

        def park():
            # Wake sources react to the *transition*, so re-arm every input the
            # next poke is about to assert.
            for name, val in (("sensor-accel-x", 0.0), ("sensor-accel-y", 0.0),
                              ("sensor-accel-z", 9.80665), ("sensor-gyro-x", 0.0),
                              ("sensor-gyro-y", 0.0), ("sensor-gyro-z", 0.0),
                              ("sensor-charging", False)):
                qmp.qom_set(name, val)
            time.sleep(1.5)

        def pokes():
            for spec in args.poke:
                key, _, val = spec.partition("=")
                val = val.strip()
                if val in ("true", "false"):
                    num = (val == "true")
                else:
                    try:
                        num = float(val)
                    except ValueError:
                        num = val
                ok, err = qmp.qom_set(key.strip(), num)
                if not ok:
                    print(f"  poke {spec} failed: {err}")

        report["catalog_before"] = str(qmp.qom_get("install-catalog"))[:600]
        def do_install():
            ok, err = qmp.qom_set("install-file", f"16:{face}")
            print(f"install-file 16:{face.name} -> {'ok' if ok else 'FAIL ' + err}")
            report["install"] = {"ok": ok, "error": err, "face": str(face)}
            time.sleep(args.settle)
            report["catalog_after"] = str(qmp.qom_get("install-catalog"))[:1200]
            print("catalog:", report["catalog_after"][:500])

        if face and args.install_early:
            do_install()
        dump(qmp, args.out, "00-boot")
        if args.burst:
            n_s, _, gap_s = args.burst.partition(":")
            count, gap = int(n_s), float(gap_s or 15)
            bx, by = (int(v) for v in args.burst_tap.split(",")) if args.burst_tap \
                else (WIDTH // 2, HEIGHT // 2)
            hashes = {}
            for k in range(count):
                time.sleep(gap)
                if args.walk:
                    for swing in range(args.walk):
                        qmp.qom_set("sensor-accel-x", 0.0)
                        qmp.qom_set("sensor-accel-y", 9.80665)
                        qmp.qom_set("sensor-accel-z", 0.0)
                        qmp.qom_set("sensor-gyro-y", 3.0)
                        time.sleep(0.31)
                        qmp.qom_set("sensor-accel-x", 0.0)
                        qmp.qom_set("sensor-accel-y", -4.0)
                        qmp.qom_set("sensor-accel-z", 9.0)
                        qmp.qom_set("sensor-gyro-y", -3.0)
                        time.sleep(0.31)
                    qmp.qom_set("sensor-gyro-y", 0.0)
                if args.bpm:
                    qmp.qom_set("sensor-pulse-bpm", args.bpm)
                    qmp.qom_set("sensor-pulse-amplitude", 1.0)
                if args.poke:
                    park()
                pokes()
                if args.burst_gesture:
                    swipe(qmp, *GESTURES[args.burst_gesture])
                else:
                    tap(qmp, bx, by)
                    if args.burst_dbl:
                        time.sleep(0.12)
                        tap(qmp, bx, by)
                path = dump(qmp, args.out, f"b{k:02d}")
                if path:
                    digest = hashlib.sha256(path.read_bytes()).hexdigest()[:12]
                    hashes[f"b{k:02d}"] = digest
            report["burst"] = hashes
            print("burst frames:", json.dumps(hashes))
            # If the guest is still polling the sensor bus it is alive, which
            # separates "panel asleep" from "UI thread wedged".
            state = str(qmp.qom_get("sensor-state"))
            report["sensor-state"] = state[:800]
            print("sensor-state:", state[:600])
        if args.btpeek:
            obs = {}
            for prop in ("bt-synthetic-last-write", "bt-synthetic-last-ap-packet",
                         "bt-synthetic-last-bt-packet", "bt-le-passkey",
                         "bt-passkey", "bt-smp-just-works"):
                obs[prop] = str(qmp.qom_get(prop))[:400]
            # drain what the guest's controller emitted
            drained = []
            for _ in range(40):
                v = str(qmp.qom_get("bt-synthetic-pop-ap-packet"))
                if not v or v in ("", "None", "{}"):
                    break
                drained.append(v[:200])
            obs["popped_ap_packets"] = drained[:20]
            report["btpeek"] = obs
            print(json.dumps(obs, ensure_ascii=False, indent=2)[:1500])
        report["memdumps"] = []
        for spec in args.memdump:
            try:
                addr_s, _, size_s = spec.partition(":")
                addr, size = int(addr_s, 0), int(size_s or 0, 0)
            except ValueError:
                print(f"  bad --memdump {spec!r}")
                continue
            path = args.out / f"mem-{addr:08x}.bin"
            r = qmp.call("pmemsave", filename=str(path), size=size, val=addr)
            if "error" in r:
                print(f"  pmemsave {addr:#x}+{size} failed: {r['error']['desc']}")
                report["memdumps"].append({"start": hex(addr), "error": r["error"]["desc"]})
            else:
                print(f"  pmemsave {addr:#x}+{size} -> {path.name} ({path.stat().st_size} B)")
                report["memdumps"].append({"start": hex(addr), "size": size,
                                           "file": path.name})
        for i, raw in enumerate(args.tap, start=1):
            if raw in GESTURES:
                swipe(qmp, *GESTURES[raw])
                time.sleep(args.settle)
                dump(qmp, args.out, f"0{i}-swipe{raw}")
                continue
            if raw.startswith("lp:"):
                try:
                    lx, _, lsec = raw[3:].rpartition(",")
                    tx, ty = (int(v) for v in lx.split(","))
                    hold = float(lsec)
                except ValueError:
                    print(f"  bad --tap {raw!r} (want lp:X,Y,SECONDS)")
                    continue
                tap(qmp, tx, ty, hold=hold)
                time.sleep(args.settle)
                dump(qmp, args.out, f"0{i}-longpress{tx}x{ty}")
                continue
            try:
                tx, ty = (int(v) for v in raw.split(","))
            except ValueError:
                print(f"  bad --tap {raw!r}")
                continue
            tap(qmp, tx, ty)
            time.sleep(args.settle)
            dump(qmp, args.out, f"0{i}-tap{tx}x{ty}")

        def gesture(spec, tag):
            """Replay one --tap style action; used after the install."""
            if spec in GESTURES:
                swipe(qmp, *GESTURES[spec])
                return dump(qmp, args.out, f"{tag}-swipe{spec}")
            if spec.startswith("lp:"):
                lx, _, lsec = spec[3:].rpartition(",")
                tx, ty = (int(v) for v in lx.split(","))
                tap(qmp, tx, ty, hold=float(lsec))
                return dump(qmp, args.out, f"{tag}-longpress{tx}x{ty}")
            tx, ty = (int(v) for v in spec.split(","))
            tap(qmp, tx, ty)
            return dump(qmp, args.out, f"{tag}-tap{tx}x{ty}")

        if face:
            if not args.install_early:
                do_install()
            dump(qmp, args.out, "01-installed")
            if args.reset_after_install:
                print("system_reset -> firmware re-scans the watchface list")
                qmp.call("system_reset")
                time.sleep(args.boot_wait)
                dump(qmp, args.out, "r0-after-reset")
        if args.post_tap or face:
            time.sleep(args.settle)
            if args.poke:
                park()
            pokes()
        # The charging page owns the screen and swallows every gesture, so use
        # the plug-in edge only to light the panel and unplug before touching
        # anything. Every later gesture resets the inactivity timer, so a short
        # gap keeps one continuous awake window.
        qmp.qom_set("sensor-charging", False)
        injects = list(args.inject)
        if args.phone_link:
            injects.insert(0, PHONE_LINK_EVENT)
        for k, hexs in enumerate(injects, start=1):
            ok, err = qmp.qom_set("bt-synthetic-inject", hexs)
            print(f"inject #{k} {hexs[:28]}... -> {'ok' if ok else 'FAIL ' + err}")
            time.sleep(4)
            dump(qmp, args.out, f"i{k:02d}-after-inject")
            if args.btpeek_late:
                late = {}
                for prop in ("bt-synthetic-last-ap-packet", "bt-synthetic-last-bt-packet",
                             "bt-synthetic-last-ap-channel", "bt-le-passkey",
                             "bt-smp-just-works"):
                    late[prop] = str(qmp.qom_get(prop))[:300]
                drained = []
                for _ in range(40):
                    v = str(qmp.qom_get("bt-synthetic-pop-ap-packet"))
                    if not v or v in ("", "None", "{}", "0:"):
                        break
                    drained.append(v[:240])
                late["ap_packets_after_inject"] = drained[:20]
                report.setdefault("btpeek_late", []).append(late)
                print(f"late peek #{k}:", json.dumps(late, ensure_ascii=False)[:900])
        if args.phone_session:
            session_face = args.phone_session.resolve()
            if not session_face.is_file():
                print(f"  --phone-session: no such face {session_face}")
            else:
                run_phone_session(qmp, session_face, args.out, report)
                time.sleep(args.settle)
                dump(qmp, args.out, "s01-after-phone-session")
        for k, spec in enumerate(args.post_tap, start=1):
            gesture(spec, f"p{k:02d}")
            time.sleep(args.post_gap)
        if face:
            time.sleep(args.extra_wait)
            qmp.qom_set("sensor-charging", False)
            dump(qmp, args.out, "02-settled")
        qmp.close()
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        log.close()
    (args.out / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), "utf8")
    print("shots:", sorted(p.name for p in args.out.glob("*.png")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
