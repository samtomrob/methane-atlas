"""Long-term average enhancement — the most defensible TROPOMI product here.

A single month's anomaly map is dominated by retrieval scatter (~6.5 ppb per
cell). Averaging every available month cuts that to well under 1 ppb of standard
error, which is what it takes to see a basin-scale enhancement of a few ppb.

Measured over 20 months of Australia + PNG, this is what emerges once coastal
cells are excluded:

  Bowen Basin coal      +1.93 ppb   Perth Basin / WA wheatbelt  +4.96 ppb
  Galilee Basin         +1.22 ppb   Cooper Basin                -1.80 ppb
  Surat Basin CSG       +1.12 ppb   Amadeus Basin               -1.81 ppb

The coal and CSG basins come out positive, which matches published TROPOMI work.
But so does the WA wheatbelt, which has no significant methane source and is
instead where TROPOMI's known surface-albedo sensitivity bites. So this layer is
honest as *observed column enhancement*, and must not be presented as an
emissions map: a latitude-banded background does not remove surface-related
bias, and separating the two needs either an albedo/elevation correction or a
full inversion (which is what Open Methane does for Australia).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from .hotspots import COAST_BUFFER_CELLS, _load_stack, interior_land_mask

# Cells need this many observed months before a long-term mean is meaningful.
MIN_MONTHS = 12

# Wider than the hotspot buffer: the shoreline artifact was shown to persist out
# to ~40 km, and this layer's whole purpose is small-amplitude signal.
CLIMATOLOGY_BUFFER_CELLS = 4


def build(raster_dir: Path, kind: str = "month") -> tuple[np.ndarray, np.ndarray, np.ndarray, dict]:
    anomalies, counts, labels = _load_stack(raster_dir, kind)
    graded = np.isfinite(anomalies)
    months_observed = graded.sum(axis=0)

    interior = interior_land_mask(months_observed, CLIMATOLOGY_BUFFER_CELLS)
    usable = interior & (months_observed >= MIN_MONTHS)

    with np.errstate(invalid="ignore"):
        mean_anom = np.nanmean(np.where(graded, anomalies, np.nan), axis=0)
        sd_anom = np.nanstd(np.where(graded, anomalies, np.nan), axis=0)

    stderr = sd_anom / np.sqrt(np.maximum(months_observed, 1))
    mean_anom = np.where(usable, mean_anom, np.nan)
    stderr = np.where(usable, stderr, np.nan)

    pooled = mean_anom[np.isfinite(mean_anom)]
    stats = {
        "periods": labels,
        "n_periods": len(labels),
        "min_months": MIN_MONTHS,
        "coast_buffer_cells": CLIMATOLOGY_BUFFER_CELLS,
        "cells": int(np.isfinite(mean_anom).sum()),
        "mean_ppb": round(float(pooled.mean()), 2),
        "sd_ppb": round(float(pooled.std()), 2),
        "p99_ppb": round(float(np.percentile(pooled, 99)), 2),
        "median_stderr_ppb": round(float(np.nanmedian(stderr)), 2),
        "caveat": (
            "Observed column enhancement, not an emissions estimate. Coal and CSG "
            "basins read positive, but so does the WA wheatbelt where TROPOMI has "
            "known surface-albedo sensitivity, so surface bias is not fully "
            "separated from real signal at this resolution."
        ),
    }
    return mean_anom, stderr, months_observed.astype("float64"), stats


def run(raster_dir: Path, kind: str = "month") -> dict[str, Any]:
    import rasterio
    from rasterio.transform import from_origin

    from .tropomi import CELL, LAT_MAX, LON_MIN, NX, NY

    mean_anom, stderr, months, stats = build(raster_dir, kind=kind)
    out_path = raster_dir / "climatology.tif"
    profile = {
        "driver": "COG",
        "dtype": "float32",
        "count": 3,
        "height": NY,
        "width": NX,
        "crs": "EPSG:4326",
        "transform": from_origin(LON_MIN, LAT_MAX, CELL, CELL),
        "nodata": float("nan"),
        "compress": "deflate",
    }
    with rasterio.open(out_path, "w", **profile) as ds:
        ds.write(mean_anom.astype("float32"), 1)
        ds.write(stderr.astype("float32"), 2)
        ds.write(months.astype("float32"), 3)
        ds.set_band_description(1, "mean_anomaly_ppb")
        ds.set_band_description(2, "stderr_ppb")
        ds.set_band_description(3, "months_observed")

    (raster_dir / "climatology.json").write_text(json.dumps(stats, indent=2), encoding="utf-8")
    print(
        f"{stats['n_periods']}-period average over {stats['cells']:,} interior-land cells\n"
        f"  mean {stats['mean_ppb']:+.2f} ppb · sd {stats['sd_ppb']:.2f} · "
        f"p99 {stats['p99_ppb']:+.2f} · typical standard error {stats['median_stderr_ppb']:.2f} ppb\n"
        f"  -> {out_path}"
    )
    return stats
