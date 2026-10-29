#!/usr/bin/env python3
"""One command: any .face in, a firmware-rendered screenshot out.

This is the watch-face debug loop the QEMU port exists for. It hides the three
steps you would otherwise chain by hand:

  1. rewrite the container's face id to match the /resource romfs directory the
     slot lives in -- the firmware cross-checks them and silently falls back to
     another face otherwise;
  2. overwrite that slot's payload in place, touching no record field (the +0
     next-record pointer makes any size change invalid);
  3. boot the emulator past the setup wizard, keep the panel awake with a
     charger plug-in *edge*, and dump the panel.

  python3 tools/face_render.py ../../miband10pro-face/native/design.face
  python3 tools/face_render.py some.face --seconds 240 --out /tmp/shot.png

The pristine firmware images are never modified; the patched resource image is
written to a scratch path and deleted unless --keep-image is given.
"""
import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
MODEL = ROOT / "models/band10pro"
SWAP = HERE / "romfs_face_swap.py"
RUNNER = HERE / "face_debug_p67.py"
PY = sys.executable

# The 17 factory slots, biggest first -- picked up at runtime, not hardcoded.
DEFAULT_AP = MODEL / "firmware/vela_ap_bound2.bin"
# The face the firmware draws on a factory-fresh /data. Auto-picking the biggest
# slot is useless if it is not this one -- the main screen would still show the
# default face. Override with --slot, or navigate the on-band picker for others.
DEFAULT_SLOT = "120917390547"


def run(argv, capture=False):
    proc = subprocess.run(argv, text=True, capture_output=capture)
    if proc.returncode:
        detail = (proc.stdout or "") + (proc.stderr or "")
        sys.exit(detail.strip() or f"failed: {' '.join(map(str, argv))}")
    return proc.stdout or ""


def slots(image):
    """Parse `romfs_face_swap.py list` -> [(face id, slot bytes)]."""
    out = run([PY, str(SWAP), "list", str(image)], capture=True)
    rows = []
    for line in out.splitlines():
        m = re.match(r"^(\S+)\s+len=\s*([\d,]+)\b", line.strip())
        if m:
            rows.append((m.group(1), int(m.group(2).replace(",", ""))))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("face", type=Path)
    ap.add_argument("--slot", help=f"factory face id to displace "
                                   f"(default: {DEFAULT_SLOT}, the face a fresh /data draws)")
    ap.add_argument("--out", type=Path, default=ROOT / "userdata/face-render")
    ap.add_argument("--seconds", type=float, default=70.0, help="guest boot budget")
    ap.add_argument("--frames", type=int, default=4, help="panel samples to take")
    ap.add_argument("--interval", type=float, default=12.0)
    ap.add_argument("--keep-image", action="store_true")
    args = ap.parse_args()

    face = args.face.resolve()
    if not face.is_file():
        sys.exit(f"no such face: {face}")
    payload = face.read_bytes()
    if payload[:4] != bytes.fromhex("5aa53412"):
        sys.exit(f"{face.name} is not a .face container")

    base = MODEL / "firmware/vela_resource.bin"
    room = {fid: size for fid, size in slots(base)}
    if not room:
        sys.exit("could not enumerate the /resource face slots")
    fits = [(fid, size) for fid, size in room.items() if size >= len(payload)]
    if not fits:
        sys.exit(f"{face.name} is {len(payload):,} B and the largest slot is "
                 f"{max(room.values()):,} B -- shrink the face first")
    if args.slot:
        slot = args.slot
    elif any(fid == DEFAULT_SLOT for fid, _ in fits):
        slot = DEFAULT_SLOT
    else:
        slot = max(fits, key=lambda t: t[1])[0]
        print(f"note: default slot {DEFAULT_SLOT} cannot hold this face; using {slot}. "
              f"The main screen will show the default face, not yours -- either shrink "
              f"the face below {room[DEFAULT_SLOT]:,} B or pick it from the on-band list.")
    if slot not in room:
        sys.exit(f"unknown slot {slot}; available: {', '.join(sorted(room))}")
    if room[slot] < len(payload):
        sys.exit(f"slot {slot} holds {room[slot]:,} B, face is {len(payload):,} B")

    scratch = Path(tempfile.mkdtemp(prefix="face-render-"))
    renamed = scratch / "renamed.face"
    image = MODEL / "firmware/vela_resource_render.bin"
    print(f"{face.name}: {len(payload):,} B -> slot {slot} ({room[slot]:,} B)")
    print(run([PY, str(SWAP), "setid", str(face), slot, str(renamed)], capture=True).strip())
    print(run([PY, str(SWAP), "swap-keep", str(base), str(image), str(renamed), slot],
              capture=True).strip())

    if not DEFAULT_AP.is_file():
        sys.exit(f"missing wizard-bypass AP image: {DEFAULT_AP}")
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    for stale in list(out.glob("*.png")) + list(out.glob("*.ppm")):
        stale.unlink()
    argv = [PY, "-u", str(RUNNER), "--ap", str(DEFAULT_AP), "--resource", str(image),
            "--fresh-journal", "--boot-wait", str(args.seconds),
            "--burst", f"{args.frames}:{args.interval}",
            "--poke", "sensor-charging=true", "--out", str(out)]
    try:
        run(argv, capture=True)
    finally:
        if args.keep_image:
            print(f"patched resource image kept at {image}")
        else:
            image.unlink(missing_ok=True)
            shutil.rmtree(scratch, ignore_errors=True)

    shots = sorted(out.glob("b*.png"), key=lambda p: p.name)
    if not shots:
        sys.exit("the emulator produced no panel samples")
    print("\nsamples:")
    for s in shots:
        print(f"   {s}")
    print(f"\npick the last one that shows the face (the panel sleeps between samples; "
          f"--poke sensor-charging=true re-wakes it every {args.interval:g}s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
