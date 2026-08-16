"""Generate the app icon: a plume dispersing from a point source.

Same mark as the site's favicon, drawn at the sizes Windows actually asks for.
Written with zlib and struct rather than an image library so the build has no
extra dependency — PNG-in-ICO is supported from Vista onward.
"""

from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

SIZES = (256, 128, 64, 48, 32, 16)
BG = (14, 21, 25)          # matches the app's dark surface
HOT = (255, 122, 47)       # the accent orange
WARM = (254, 194, 135)     # plume tail


def _px(size: int) -> bytes:
    """One RGBA image: rounded dark tile, bright source, plume to the north-east."""
    s = size
    radius = s * 0.18
    src = (s * 0.34, s * 0.70)          # source point, lower-left
    rows = []
    for y in range(s):
        row = bytearray()
        for x in range(s):
            # Rounded-rectangle mask.
            dx = max(radius - x, 0, x - (s - radius))
            dy = max(radius - y, 0, y - (s - radius))
            inside = math.hypot(dx, dy) <= radius
            if not inside:
                row += bytes((0, 0, 0, 0))
                continue

            r, g, b = BG
            # Plume: a cone opening toward the upper right, densest at the source.
            vx, vy = x - src[0], y - src[1]
            dist = math.hypot(vx, vy)
            if dist > 0.5:
                ang = math.atan2(-vy, vx)              # screen y grows downward
                spread = abs(ang - math.radians(42))
                reach = dist / (s * 0.62)
                if spread < 0.85 and reach < 1.25:
                    # Fade with angle from the axis and distance travelled.
                    strength = (1 - spread / 0.85) ** 1.6 * max(0.0, 1 - reach * 0.75)
                    if strength > 0.02:
                        mix = min(1.0, strength * 1.7)
                        tail = min(1.0, reach * 0.9)
                        pr = HOT[0] * (1 - tail) + WARM[0] * tail
                        pg = HOT[1] * (1 - tail) + WARM[1] * tail
                        pb = HOT[2] * (1 - tail) + WARM[2] * tail
                        r = r * (1 - mix) + pr * mix
                        g = g * (1 - mix) + pg * mix
                        b = b * (1 - mix) + pb * mix
            # The source itself, always the brightest thing.
            core = max(0.0, 1 - dist / (s * 0.10))
            if core > 0:
                k = min(1.0, core * 1.5)
                r = r * (1 - k) + 255 * k
                g = g * (1 - k) + 190 * k
                b = b * (1 - k) + 140 * k
            row += bytes((int(r), int(g), int(b), 255))
        rows.append(bytes(row))
    return b"".join(b"\x00" + r for r in rows)


def _png(size: int) -> bytes:
    raw = _px(size)

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def main() -> None:
    images = [(s, _png(s)) for s in SIZES]
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = len(header) + 16 * len(images)
    entries, blobs = b"", b""
    for size, blob in images:
        dim = 0 if size >= 256 else size          # 0 means 256 in the ICO format
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(blob), offset)
        offset += len(blob)
        blobs += blob
    dest = Path(__file__).with_name("methane-atlas.ico")
    dest.write_bytes(header + entries + blobs)
    print(f"wrote {dest.name}  {dest.stat().st_size/1024:.0f} KB  sizes {SIZES}")


if __name__ == "__main__":
    main()
