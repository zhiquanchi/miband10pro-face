#!/usr/bin/env python3
"""Swap a watch face into a factory slot of the Band 10 Pro /resource romfs.

This is the emulator-side render loop: the firmware loads faces straight out of
the read-only /resource partition, so replacing one slot makes the guest draw
*our* container with no install, no Bluetooth and no on-band face picker.

Record layout (verified 17/17 against the factory faces, big-endian fields):

    +0   u32   opaque (subtree size / checksum — not needed here)
    +4   u32   first child (directory) or 0 (file)
    +8   u32   payload length
    +12  u32   opaque
    +16  char  name, NUL terminated, padded up to 4
    ..   bytes payload

Because the swap is done **in place** and the slot's payload is never moved,
every pointer in the image stays valid; the only field that has to change is the
length at +8, and any leftover bytes of the old face become unreachable padding.
That is what makes this safe to try, unlike rewriting the littlefs NV partition.

  python3 tools/romfs_face_swap.py list <resource.bin>
  python3 tools/romfs_face_swap.py setid <in.face> <faceId> <out.face>
  python3 tools/romfs_face_swap.py swap-keep <resource.bin> <out.bin> <new.face> [faceId]
  python3 tools/romfs_face_swap.py swap <resource.bin> <faceId> <new.face> <out.bin>

`swap-keep` is the one that works: it overwrites the payload and touches no
record field, and `setid` makes the container's own id match the romfs directory
name, which the firmware cross-checks before it will draw the face.
"""
import re
import struct
import sys
from pathlib import Path

MAGIC = b"\x5a\xa5\x34\x12"
NAME = b"resource.bin\x00"
ID_OFF = 40          # ASCII watchface id inside the .face/.bin global header
ID_LEN = 24
align4 = lambda n: (n + 3) & ~3


def slots(blob: bytes):
    """Yield (face_id, record_off, data_off, length) for every factory face."""
    for m in re.finditer(re.escape(NAME), blob):
        rec = m.start() - 16
        if rec < 16:
            continue
        length = struct.unpack_from(">I", blob, rec + 8)[0]
        data = align4(m.start() + len(NAME))
        if blob[data:data + 4] != MAGIC:
            continue
        face_id = blob[data + ID_OFF:data + ID_OFF + ID_LEN].split(b"\x00")[0].decode("ascii", "replace")
        yield face_id, rec, data, length


def main(argv):
    cmd, img_path = argv[1], Path(argv[2])
    if cmd == "setid":
        # The firmware cross-checks the container's face id against the romfs
        # directory it loads it from; a mismatch makes it fall back to another
        # face instead of drawing ours. Rewriting the id is a single 11-byte
        # edit at +0x28 (that offset is the only place the id appears, in ours
        # and in every factory container).
        #   setid <in.face> <newId> <out.face>
        face = Path(argv[2])
        new_id = argv[3].encode("ascii")
        out = Path(argv[4])
        blob = bytearray(face.read_bytes())
        if blob[:4] != MAGIC:
            sys.exit(f"{face} does not start with the .face magic")
        if not 5 <= len(new_id) <= ID_LEN - 1:
            sys.exit(f"id {argv[3]!r} must be 5..{ID_LEN - 1} ASCII characters")
        blob[ID_OFF:ID_OFF + ID_LEN] = new_id + b"\x00" * (ID_LEN - len(new_id))
        out.write_bytes(bytes(blob))
        print(f"{face.name}: id @{ID_OFF} -> {argv[3]!r}, {len(blob):,} B -> {out}")
        return 0
    blob = bytearray(img_path.read_bytes())
    table = list(slots(bytes(blob)))
    if cmd == "list":
        for face_id, rec, data, length in sorted(table):
            print(f"{face_id:<16} len={length:>10,}  rec={rec:#010x}  data={data:#010x}")
        print(f"{len(table)} faces")
        return 0
    if cmd == "swap-keep":
        """Overwrite the payload but change NO record field at all.

        The control experiment showed that editing the +8 length is what breaks
        a slot: +0 is an absolute pointer to the next record, so shrinking the
        declared size leaves the chain pointing past the face the loader then
        reads.  A .face container is self-describing, so trailing bytes after
        our payload are simply ignored — keep the slot byte-for-byte the same
        size and the whole image stays internally consistent.  Only +12 (an
        unknown, probably per-file checksum) is left stale; this mode exists to
        find out whether the loader actually consults it.
        """
        out_path = Path(argv[3])
        payload = Path(argv[4]).read_bytes()
        only = argv[5] if len(argv) > 5 else None
        if payload[:4] != MAGIC:
            sys.exit(f"{argv[4]} does not start with the .face magic")
        hit = [t for t in table if t[0] == only] if only else []
        targets = hit or [t for t in table if t[3] >= len(payload)]
        done = []
        for face_id, rec, data, length in targets:
            if len(payload) > length:
                continue
            blob[data:data + length] = payload + bytes(length - len(payload))
            done.append(face_id)
        out_path.write_bytes(bytes(blob))
        print(f"kept slot sizes, overwrote {len(done)} slot(s) {done} with {len(payload):,} B; "
              "no record field touched")
        print(f"verify: {len(list(slots(out_path.read_bytes())))}/17 faces still enumerable")
        return 0
    if cmd == "swap-all":
        out_path = Path(argv[3])
        payload = Path(argv[4]).read_bytes()
        if payload[:4] != MAGIC:
            sys.exit(f"{argv[4]} does not start with the .face magic")
        done, skipped = 0, []
        for face_id, rec, data, length in sorted(table):
            if len(payload) > length:
                skipped.append((face_id, length))
                continue
            blob[data:data + len(payload)] = payload
            struct.pack_into(">I", blob, rec + 8, len(payload))
            done += 1
        out_path.write_bytes(bytes(blob))
        print(f"swapped {done}/{len(table)} slots with {len(payload):,} B; "
              f"too small: {skipped}")
        after = list(slots(out_path.read_bytes()))
        print(f"verify: {len(after)}/17 faces still enumerable")
        return 0
    if cmd != "swap":
        sys.exit("usage: romfs_face_swap.py {list|swap|swap-all} <image> ...")
    want, face_path, out_path = argv[3], Path(argv[4]), Path(argv[5])
    payload = face_path.read_bytes()
    if payload[:4] != MAGIC:
        sys.exit(f"{face_path.name} does not start with the .face magic")
    hit = [t for t in table if t[0] == want]
    if not hit:
        sys.exit(f"no slot for face id {want}; known: {[t[0] for t in table]}")
    face_id, rec, data, length = hit[0]
    if len(payload) > length:
        sys.exit(f"{face_path.name} is {len(payload):,} B but slot {want} only holds {length:,} B; "
                 "shrink the face (fewer/less colourful raster blocks) or pick a bigger slot")
    blob[data:data + len(payload)] = payload
    struct.pack_into(">I", blob, rec + 8, len(payload))
    out_path.write_bytes(bytes(blob))
    print(f"swapped {face_path.name} ({len(payload):,} B) into {want} "
          f"(was {length:,} B) at {data:#x}; length field {rec + 8:#x} -> {len(payload):,}")
    check = list(slots(out_path.read_bytes()))
    got = [t for t in check if t[0] == want]
    print("verify:", got[0] if got else "FACE ID NOT READABLE AFTER SWAP")
    print(f"total faces still enumerable: {len(check)}/17")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
