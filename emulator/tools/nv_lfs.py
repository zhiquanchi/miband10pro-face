#!/usr/bin/env python3
"""Inspect / edit the emulated Band 10 Pro NV littlefs image.

`config/factory/nv.img` is a genuine littlefs volume (magic "littlefs" at
offset 0) produced by models/band10pro/tools/initialize_factory.py. The guest
reports `lfs_dir_fetchmatch: Corrupted dir pair at {0x0, 0x1}` for its userdata
mount, so NV is the one persistent store that is known-good in this
configuration — the place to look for bind / setup-wizard state.

Usage:
    python3 tools/nv_lfs.py list                # dump the tree
    python3 tools/nv_lfs.py cat <path>          # hexdump one file
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NV = ROOT / "models/band10pro/config/factory/nv.img"

import littlefs  # noqa: E402


def walk(fs, path="/", depth=0):
    entries = []
    try:
        for e in fs.listdir(path):
            name = e["name"] if isinstance(e, dict) else e.name
            mode = e["type"] if isinstance(e, dict) else None
            full = name if path == "/" else f"{path}/{name}"
            size = (e.get("size") if isinstance(e, dict) else getattr(e, "size", None))
            entries.append((full, mode, size, depth))
            if mode in (littlefs.LFS_TYPE_DIR, 1, "dir") or (
                    isinstance(mode, int) and mode == 1):
                entries.extend(walk(fs, full, depth + 1))
    except Exception as exc:
        entries.append((f"{path} <error: {exc}>", None, None, depth))
    return entries


def main():
    action = sys.argv[1] if len(sys.argv) > 1 else "list"
    # never operate on the factory image in place
    work = Path(f"/tmp/nv_work.img")
    work.write_bytes(NV.read_bytes())
    fs = littlefs.LittleFS(str(work), block_size=256)
    fs.mount()
    if action == "list":
        print(f"{NV} ({NV.stat().st_size:,} bytes)")
        for full, mode, size, depth in walk(fs):
            kind = {0: "file", 1: "dir"}.get(mode if isinstance(mode, int) else None, mode)
            print(f"  {'  ' * depth}{full}  [{kind}] {size if size is not None else ''}")
    elif action == "cat":
        target = sys.argv[2]
        data = fs.read(target)
        print(f"{target}: {len(data)} bytes")
        for i in range(0, min(len(data), 512), 16):
            chunk = data[i:i + 16]
            print(f"  {i:04x}  {chunk.hex(' '):<47s}  "
                  f"{''.join(chr(c) if 32 <= c < 127 else '.' for c in chunk)}")
    fs.close()


if __name__ == "__main__":
    main()
