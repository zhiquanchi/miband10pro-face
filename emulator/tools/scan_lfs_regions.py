#!/usr/bin/env python3
"""Look for a mountable littlefs in the emulated Band 10 Pro persistent regions.

The guest logs `lfs_dir_fetchmatch: Corrupted dir pair at {0x0, 0x1}`, i.e. the
userdata littlefs superblock pair is erased, so nothing under `persist.*` can
ever be written. This checks each candidate region for a real littlefs
superblock so we know where a valid one has to be provided.

littlefs v2 block header: the first two words of a superblock block are the
commit pair's tag CRC + tag; a valid superblock pair has identical metadata in
blocks 0 and 1. Erased flash is all 0xFF.
"""
from pathlib import Path

CANDIDATES = [
    ("nor-seed.bin", "models/band10pro/config/factory/nor-seed.bin"),
    ("nv.img", "models/band10pro/config/factory/nv.img"),
    ("flash.journal", "models/band10pro/userdata/flash.journal"),
]
BLOCK_SIZES = (256, 512, 1024, 2048, 4096, 8192, 16384, 32768)
ROOT = Path(__file__).resolve().parents[1]


def erased(b, off, size):
    return b[off:off + size] == b"\xff" * size


def main():
    for name, rel in CANDIDATES:
        p = ROOT / rel
        if not p.exists():
            print(f"--- {name}: missing")
            continue
        b = p.read_bytes()
        print(f"\n--- {name} ({len(b):,} B) ---")
        print(f"    head: {b[:24].hex(' ')}")
        print(f"    first 4 KiB erased: {erased(b, 0, 4096)}   "
              f"all erased: {b.count(0xFF) == len(b)}")
        # look for littlefs superblock magic patterns at block boundaries
        found = 0
        for bs in BLOCK_SIZES:
            for off in range(0, min(len(b), 32 * 1024 * 1024), bs):
                blk = b[off:off + 16]
                if len(blk) < 16 or erased(b, off, bs):
                    continue
                # littlefs v2 superblock: tag = 0x00000000 pair header words
                if blk[:4] == b"\x00\x00\x00\x00" and blk[4:8] != b"\x00\x00\x00\x00":
                    found += 1
                    if found <= 6:
                        print(f"    candidate superblock @ {off:#x} (block size {bs}): "
                              f"{blk.hex(' ')}")
                    break
            if found:
                break
        if not found:
            print("    no plausible littlefs superblock found")


if __name__ == "__main__":
    main()
