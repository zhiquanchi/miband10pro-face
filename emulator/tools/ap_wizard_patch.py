#!/usr/bin/env python3
"""Force the AP's `persist.setup_wizard` read to a constant, so the emulator can
be booted past the setup wizard without a Bluetooth bind.

At file offset 0x64644c the AP does:

    ldr  r0, [pc, #0x1c0]   ; "persist.setup_wizard"
    bl   #0x2c3dafe0        ; property getter -> r0
    mov  r3, r0
    ...
    strb r3, [r4]           ; latch into the global config struct

Replacing the load+call with `movs r0, #N` + nops makes the latched value
constant. Which N means "wizard finished" is not known statically, so both
candidates are emitted and each is checked by booting and dumping the panel.

Usage:
    python3 tools/ap_wizard_patch.py            # write both variants
    python3 tools/ap_wizard_patch.py --verify   # show the original bytes
"""
import argparse
import shutil
import struct
from pathlib import Path

AP = Path("/tmp/fw10pro/vela_ap.bin")
OUT = Path(__file__).resolve().parents[1] / "models/band10pro/firmware"
SITE = 0x64_644C          # file offset of the ldr
SIZE = 8                  # ldr.W (4) + bl.W (4)

# Thumb: MOVS r0,#imm  => 0x20|imm ; NOP => 0xBF00
def patch(value: int) -> bytes:
    return struct.pack("<HHHH", 0x2000 | (value & 0xFF), 0xBF00, 0xBF00, 0xBF00)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify", action="store_true")
    args = ap.parse_args()
    raw = AP.read_bytes()
    orig = raw[SITE:SITE + SIZE]
    print(f"site {SITE:#x} original bytes: {orig.hex(' ')}")
    # decode the original two instructions as a sanity check
    ldr, bl = struct.unpack_from("<HH", orig, 0)
    print(f"  halfword0 {ldr:#06x} -> {'LDR r0,[pc,#imm]' if ldr >> 11 == 0b01001 and (ldr >> 8) == 0b01001 else 'not 16-bit LDR-literal'}")
    if args.verify:
        return
    if (ldr >> 11) != 0b01001:
        raise SystemExit(f"unexpected opcode at {SITE:#x}: {orig.hex(' ')}")

    for value in (0, 1):
        dst = OUT / f"vela_ap_wiz{value}.bin"
        buf = bytearray(raw)
        buf[SITE:SITE + SIZE] = patch(value)
        dst.write_bytes(bytes(buf))
        print(f"wrote {dst.name}: movs r0,#{value} at {SITE:#x}  "
              f"({buf[SITE:SITE + SIZE].hex(' ')})")


if __name__ == "__main__":
    main()
