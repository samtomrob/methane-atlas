"""Render methane composites to web-ready PNG overlays.

At 0.05° over a fixed region there is no sub-pixel detail for a tile pyramid to
reveal, so each period becomes one georeferenced PNG that MapLibre drapes as an
`image` source. ~250 KB per period per band, no tile server, no tiling toolchain.

Colour scales are computed once across every period and then held fixed, so
stepping through the time slider compares like with like.
"""

from __future__ import annotations

import json
import struct
import zlib
from pathlib import Path

import numpy as np

from . import ROI

LON_MIN, LAT_MIN, LON_MAX, LAT_MAX = ROI

# magma — perceptually uniform, colour-blind safe, reads as "intensity"
MAGMA = np.array(
    [
        (0, 0, 4),
        (28, 16, 68),
        (79, 18, 123),
        (129, 37, 129),
        (181, 54, 122),
        (229, 80, 100),
        (251, 135, 97),
        (254, 194, 135),
        (252, 253, 191),
    ],
    dtype="float64",
)

# Diverging blue→neutral→red for anomaly: cool means below the latitude
# background, warm means enhanced.
DIVERGING = np.array(
    [
        (33, 102, 172),
        (103, 169, 207),
        (209, 229, 240),
        (247, 247, 247),
        (253, 219, 199),
        (239, 138, 98),
        (178, 24, 43),
    ],
    dtype="float64",
)


def write_png(path: Path, rgba: np.ndarray) -> None:
    """Write an 8-bit RGBA PNG. Hand-rolled to avoid a dependency for ~20 lines."""
    height, width = rgba.shape[:2]
    rows = b"".join(b"\x00" + rgba[y].tobytes() for y in range(height))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)  # 8-bit, colour type 6 = RGBA
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(rows, 6))
        + chunk(b"IEND", b"")
    )


def _ramp(values01: np.ndarray, anchors: np.ndarray) -> np.ndarray:
    """Interpolate normalised values through a colour anchor table."""
    positions = np.linspace(0.0, 1.0, len(anchors))
    out = np.empty(values01.shape + (3,), dtype="float64")
    for channel in range(3):
        out[..., channel] = np.interp(values01, positions, anchors[:, channel])
    return out


def colorize(
    data: np.ndarray, vmin: float, vmax: float, style: str
) -> np.ndarray:
    """Map a float band to RGBA. NaN becomes fully transparent."""
    valid = np.isfinite(data)
    rgba = np.zeros(data.shape + (4,), dtype="uint8")
    if not valid.any():
        return rgba

    if style == "diverging":
        limit = max(abs(vmin), abs(vmax)) or 1.0
        norm = np.clip((data + limit) / (2 * limit), 0.0, 1.0)
        rgb = _ramp(np.where(valid, norm, 0.5), DIVERGING)
        # Opacity carries magnitude: a cell sitting at the background is fully
        # transparent, so the basemap stays readable and the eye is drawn only
        # to genuine deviation rather than to a wash of near-zero noise.
        #
        # The ramp runs across the *whole* scale (not a fraction of it) and is
        # deliberately super-linear. Residual per-cell scatter sits at roughly a
        # quarter of the p98 limit, so a linear ramp would render noise at ~25%
        # opacity everywhere — a visible haze over the entire continent. At
        # exponent 1.6 that same scatter lands near 10% while real enhancement
        # at the top of the scale still reads at full strength.
        strength = np.clip(np.abs(np.where(valid, data, 0.0)) / limit, 0.0, 1.0)
        alpha = 245 * np.power(strength, 1.6)
    else:
        span = (vmax - vmin) or 1.0
        norm = np.clip((data - vmin) / span, 0.0, 1.0)
        rgb = _ramp(np.where(valid, norm, 0.0), MAGMA)
        alpha = np.full(data.shape, 235.0)

    rgba[..., :3] = rgb.astype("uint8")
    rgba[..., 3] = np.where(valid, alpha, 0).astype("uint8")
    return rgba


BANDS = {
    # band index in the COG -> (output name, colour style)
    1: ("mean", "sequential"),
    2: ("anomaly", "diverging"),
}


def run(raster_dir: Path, out_dir: Path, kind: str = "month") -> dict:
    import rasterio

    index_path = raster_dir / f"{kind}_index.json"
    if not index_path.exists():
        raise SystemExit(f"no composites found at {index_path} — run `matlas tropomi` first")
    entries = sorted(json.loads(index_path.read_text()), key=lambda e: e["period"])
    if not entries:
        raise SystemExit("composite index is empty")

    # Pass 1: fixed colour scales across every period.
    mean_samples: list[np.ndarray] = []
    anom_samples: list[np.ndarray] = []
    for entry in entries:
        with rasterio.open(raster_dir / entry["file"]) as ds:
            m = ds.read(1)
            a = ds.read(2)
        mean_samples.append(m[np.isfinite(m)][::37])
        anom_samples.append(a[np.isfinite(a)][::37])

    all_mean = np.concatenate([s for s in mean_samples if s.size])
    all_anom = np.concatenate([s for s in anom_samples if s.size])
    mean_lo, mean_hi = (float(np.percentile(all_mean, 2)), float(np.percentile(all_mean, 98)))
    anom_limit = float(np.percentile(np.abs(all_anom), 98))
    print(f"scales: mean {mean_lo:.0f}..{mean_hi:.0f} ppb · anomaly ±{anom_limit:.0f} ppb")

    # Pass 2: render.
    out_dir.mkdir(parents=True, exist_ok=True)
    periods = []
    for entry in entries:
        with rasterio.open(raster_dir / entry["file"]) as ds:
            for band, (name, style) in BANDS.items():
                data = ds.read(band)
                vmin, vmax = (
                    (-anom_limit, anom_limit) if style == "diverging" else (mean_lo, mean_hi)
                )
                png = out_dir / name / f"{entry['period']}.png"
                write_png(png, colorize(data, vmin, vmax, style))
        periods.append(
            {
                "period": entry["period"],
                "start": entry["start"],
                "coverage_pct": entry["coverage_pct"],
                "background_ppb": entry["background_ppb"],
                "max_anomaly_ppb": entry.get("max_anomaly_ppb"),
                "median_obs_per_cell": entry.get("median_obs_per_cell"),
            }
        )
        print(f"  rendered {entry['period']}")

    manifest = {
        "kind": kind,
        "bounds": [LON_MIN, LAT_MIN, LON_MAX, LAT_MAX],
        "scales": {
            "mean": {"min": round(mean_lo, 1), "max": round(mean_hi, 1), "unit": "ppb"},
            "anomaly": {
                "min": round(-anom_limit, 1),
                "max": round(anom_limit, 1),
                "unit": "ppb",
            },
        },
        "periods": periods,
        "source": "Sentinel-5P TROPOMI L2 CH4 via Copernicus Data Space",
        "attribution": "Contains modified Copernicus Sentinel data (2026)",
        "method": (
            "qa_value >= 0.5, land pixels only, binned to 0.05 degrees. Anomaly is "
            "the difference from a latitude-banded background median, shown only "
            "where a cell has at least 3 observations."
        ),
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"\n{len(periods)} periods rendered to {out_dir}")
    return manifest
