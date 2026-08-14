"""Render-stage checks. Dependency-free so CI can run it with plain python.

    python tests/test_render.py

Guards the two properties that are easy to break silently and hard to notice
until the map looks wrong: the hand-rolled PNG encoder must emit a valid 8-bit
RGBA file, and the anomaly ramp must keep background-level noise faint while
letting real enhancement reach full opacity.
"""

from __future__ import annotations

import struct
import sys
import tempfile
import zlib
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from matlas import render  # noqa: E402

failures: list[str] = []


def check(condition: bool, message: str) -> None:
    if condition:
        print(f"  ok   {message}")
    else:
        print(f"  FAIL {message}")
        failures.append(message)


def decode_png(raw: bytes) -> tuple[int, int, int, int, np.ndarray]:
    assert raw[:8] == b"\x89PNG\r\n\x1a\n", "bad signature"
    width, height, depth, ctype = struct.unpack(">IIBB", raw[16:26])
    idat = b""
    pos = 8
    while pos < len(raw):
        length = struct.unpack(">I", raw[pos : pos + 4])[0]
        if raw[pos + 4 : pos + 8] == b"IDAT":
            idat += raw[pos + 8 : pos + 8 + length]
        pos += 12 + length
    rows = np.frombuffer(zlib.decompress(idat), dtype="uint8").reshape(height, width * 4 + 1)
    return width, height, depth, ctype, rows[:, 1:].reshape(height, width, 4)


print("png encoder")
rgba = np.zeros((7, 11, 4), dtype="uint8")
rgba[..., 0] = 200
rgba[3, 5] = (1, 2, 3, 255)
with tempfile.TemporaryDirectory() as tmp:
    path = Path(tmp) / "t.png"
    render.write_png(path, rgba)
    w, h, depth, ctype, back = decode_png(path.read_bytes())
check((w, h) == (11, 7), f"dimensions round-trip ({w}x{h})")
check((depth, ctype) == (8, 6), f"8-bit RGBA header (depth={depth}, colour type={ctype})")
check(bool((back == rgba).all()), "pixels round-trip byte-exact")

print("\nnan handling")
data = np.array([[np.nan, 0.0], [10.0, np.nan]])
out = render.colorize(data, -20, 20, "diverging")
check(out[0, 0, 3] == 0 and out[1, 1, 3] == 0, "NaN cells are fully transparent")
check(out[1, 0, 3] > 0, "finite cells are painted")

print("\nanomaly opacity ramp")
limit = 20.0
# Background-level scatter should stay faint; enhancement at the top of the
# scale should be close to opaque.
noise = render.colorize(np.full((40, 40), 0.25 * limit), -limit, limit, "diverging")
signal = render.colorize(np.full((40, 40), limit), -limit, limit, "diverging")
zero = render.colorize(np.zeros((5, 5)), -limit, limit, "diverging")
noise_alpha = float(noise[..., 3].mean())
signal_alpha = float(signal[..., 3].mean())
check(zero[..., 3].max() == 0, "cells exactly at background are invisible")
check(noise_alpha < 45, f"quarter-scale scatter stays faint (alpha {noise_alpha:.0f}/255)")
check(signal_alpha > 200, f"full-scale enhancement is strong (alpha {signal_alpha:.0f}/255)")
check(
    signal_alpha > 4 * noise_alpha,
    f"signal-to-noise contrast in opacity ({signal_alpha / max(noise_alpha, 1):.1f}x)",
)

print("\nsequential ramp")
seq = render.colorize(np.array([[1850.0, 1900.0]]), 1850, 1900, "sequential")
check(seq[0, 0, 3] > 0 and seq[0, 1, 3] > 0, "concentration band paints its full range")
check(
    not np.array_equal(seq[0, 0, :3], seq[0, 1, :3]),
    "scale ends map to different colours",
)

print()
if failures:
    print(f"FAILED: {len(failures)} check(s)")
    sys.exit(1)
print("all render checks passed")
