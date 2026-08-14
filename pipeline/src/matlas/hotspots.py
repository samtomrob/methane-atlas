"""Persistent methane enhancement, cross-referenced against infrastructure.

A single month's anomaly map is mostly noise dressed as signal: retrieval scatter,
one-off cloud edges and albedo artifacts all produce plausible-looking blobs. What
distinguishes a real source is that it is *still there next month*. This stage
stacks every monthly composite and keeps only cells that are elevated in most of
the months where they were adequately observed.

Each surviving cluster is then matched to its nearest mapped infrastructure. That
match is explicitly "what is nearby", not "what caused this" — TROPOMI's ~7 km
footprint cannot attribute emissions to a facility, and the output carries a
coastal-proximity diagnostic precisely because coastlines are where this product
is most prone to artifacts.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from . import ROI

LON_MIN, LAT_MIN, LON_MAX, LAT_MAX = ROI
CELL = 0.05

# An anomaly must clear this to count as "elevated" for a month. TROPOMI's
# per-cell scatter after monthly averaging sits near 5-8 ppb, so this is roughly
# a 2-3 sigma bar rather than an arbitrary round number.
ELEVATED_PPB = 18.0

# A cell needs this many adequately-observed months before persistence means
# anything, and must be elevated in at least this fraction of them.
MIN_MONTHS_OBSERVED = 8
MIN_PERSISTENCE = 0.5

# Clusters closer than this are treated as one feature (TROPOMI's footprint is
# ~7 km, so neighbouring cells are not independent).
CLUSTER_RADIUS_DEG = 0.35

# Radius (in cells) used to judge how much no-data surrounds a hotspot. A high
# fraction means coast or scene edge, where this product is least trustworthy.
COAST_WINDOW = 4

# Cells within this many grid cells of any never-observed (ocean) cell are
# excluded outright.
#
# This is not conservatism for its own sake — it is the fix for a measured
# failure. Run without it, the persistence test returned 99 "hotspots" of which
# 13 of the top 15 hugged the coastline, most with no mapped infrastructure
# within 100+ km. TROPOMI's ~7 km footprint straddling a shoreline mixes bright
# surf, sand and water into one retrieval, which biases it systematically —
# and a systematic bias *persists*, so persistence alone cannot filter it out.
# 3 cells is ~15 km, about two footprints.
COAST_BUFFER_CELLS = 3

INFRA_LAYERS = ("coal_mines", "gas_plants", "pipelines_gas", "pipelines_oil")


@dataclass
class Hotspot:
    lon: float
    lat: float
    months_observed: int
    months_elevated: int
    mean_anomaly_ppb: float
    peak_anomaly_ppb: float
    mean_obs_per_month: float
    coastal_fraction: float
    nearest: list[dict[str, Any]] = field(default_factory=list)

    @property
    def persistence(self) -> float:
        return self.months_elevated / max(self.months_observed, 1)


def _load_stack(raster_dir: Path, kind: str) -> tuple[np.ndarray, np.ndarray, list[str]]:
    import rasterio

    index_path = raster_dir / f"{kind}_index.json"
    entries = sorted(json.loads(index_path.read_text()), key=lambda e: e["period"])
    anomalies, counts, labels = [], [], []
    for entry in entries:
        with rasterio.open(raster_dir / entry["file"]) as ds:
            anomalies.append(ds.read(2))
            counts.append(ds.read(3))
        labels.append(entry["period"])
    return np.stack(anomalies), np.stack(counts), labels


def interior_land_mask(months_observed: np.ndarray, buffer_cells: int) -> np.ndarray:
    """Cells far enough from any never-observed cell to be free of shoreline
    contamination. Ocean is never retrieved, so 'never observed' is a usable
    proxy for water without needing a coastline dataset.

    Implemented as a max-filter over the no-data mask via shifted overlays,
    which keeps this dependency-free.
    """
    nodata = months_observed == 0
    near_nodata = nodata.copy()
    for dr in range(-buffer_cells, buffer_cells + 1):
        for dc in range(-buffer_cells, buffer_cells + 1):
            if dr == 0 and dc == 0:
                continue
            if dr * dr + dc * dc > buffer_cells * buffer_cells:
                continue  # circular kernel
            shifted = np.roll(np.roll(nodata, dr, axis=0), dc, axis=1)
            # np.roll wraps; blank the wrapped edges so the ROI border is not
            # contaminated by the opposite side of the grid.
            if dr > 0:
                shifted[:dr, :] = False
            elif dr < 0:
                shifted[dr:, :] = False
            if dc > 0:
                shifted[:, :dc] = False
            elif dc < 0:
                shifted[:, dc:] = False
            near_nodata |= shifted
    return ~near_nodata


def _km_per_deg_lon(lat: float) -> float:
    return 111.32 * math.cos(math.radians(lat))


def _distance_km(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    dx = (lon2 - lon1) * _km_per_deg_lon((lat1 + lat2) / 2)
    dy = (lat2 - lat1) * 110.57
    return math.hypot(dx, dy)


def _load_infrastructure(data_dir: Path) -> list[dict[str, Any]]:
    """Flatten infrastructure into candidate points. Lines contribute their
    vertices, which is accurate enough at a 5 km grid and avoids a geometry
    dependency."""
    features: list[dict[str, Any]] = []
    for layer in INFRA_LAYERS:
        path = data_dir / f"{layer}.geojson"
        if not path.exists():
            continue
        for feat in json.loads(path.read_text(encoding="utf-8")).get("features", []):
            props = feat.get("properties") or {}
            geom = feat.get("geometry") or {}
            coords: list[tuple[float, float]] = []

            def walk(node: Any) -> None:
                if (
                    isinstance(node, (list, tuple))
                    and len(node) >= 2
                    and isinstance(node[0], (int, float))
                    and isinstance(node[1], (int, float))
                ):
                    coords.append((float(node[0]), float(node[1])))
                elif isinstance(node, (list, tuple)):
                    for child in node:
                        walk(child)

            walk(geom.get("coordinates", []))
            if not coords:
                continue
            features.append(
                {
                    "layer": layer,
                    "name": props.get("name") or "(unnamed)",
                    "subtype": props.get("subtype"),
                    "operator": props.get("operator"),
                    "state": props.get("state"),
                    "country": props.get("country"),
                    "coords": coords,
                }
            )
    return features


def _nearest_infrastructure(
    lon: float, lat: float, infra: list[dict[str, Any]], limit: int = 3
) -> list[dict[str, Any]]:
    scored: list[tuple[float, dict[str, Any]]] = []
    for feat in infra:
        best = min(_distance_km(lon, lat, c[0], c[1]) for c in feat["coords"])
        scored.append((best, feat))
    scored.sort(key=lambda s: s[0])
    out = []
    for dist, feat in scored[:limit]:
        out.append(
            {
                "layer": feat["layer"],
                "name": feat["name"],
                "subtype": feat["subtype"],
                "state": feat["state"],
                "country": feat["country"],
                "distance_km": round(dist, 1),
            }
        )
    return out


def find(raster_dir: Path, data_dir: Path, kind: str = "month") -> dict[str, Any]:
    anomalies, counts, labels = _load_stack(raster_dir, kind)
    n_months = len(labels)
    print(f"stacked {n_months} {kind}ly composites ({labels[0]} .. {labels[-1]})")

    graded = np.isfinite(anomalies)  # anomaly band is already obs-gated
    months_observed = graded.sum(axis=0)
    elevated = graded & (anomalies >= ELEVATED_PPB)
    months_elevated = elevated.sum(axis=0)

    with np.errstate(invalid="ignore"):
        mean_anom = np.nanmean(np.where(graded, anomalies, np.nan), axis=0)
        peak_anom = np.nanmax(np.where(graded, anomalies, np.nan), axis=0)
        mean_obs = np.nanmean(np.where(counts > 0, counts, np.nan), axis=0)

    # Distribution context, so the threshold choice is inspectable rather than
    # asserted.
    pooled = anomalies[graded]
    print(
        f"anomaly distribution over all months: sd {pooled.std():.1f} ppb · "
        f"p95 {np.percentile(pooled, 95):.1f} · p99 {np.percentile(pooled, 99):.1f} · "
        f"threshold {ELEVATED_PPB:.0f} ppb"
    )

    persistence = np.where(
        months_observed >= MIN_MONTHS_OBSERVED, months_elevated / np.maximum(months_observed, 1), 0.0
    )
    interior = interior_land_mask(months_observed, COAST_BUFFER_CELLS)
    persistent = (months_observed >= MIN_MONTHS_OBSERVED) & (persistence >= MIN_PERSISTENCE)
    candidates = persistent & interior

    rejected_coastal = int((persistent & ~interior).sum())
    print(
        f"cells with >= {MIN_MONTHS_OBSERVED} observed months: {int((months_observed >= MIN_MONTHS_OBSERVED).sum()):,}"
    )
    print(f"cells elevated in >= {MIN_PERSISTENCE:.0%} of those months: {int(persistent.sum()):,}")
    print(
        f"  of those, {rejected_coastal:,} rejected as within "
        f"{COAST_BUFFER_CELLS} cells (~{COAST_BUFFER_CELLS * 5.5:.0f} km) of the coast/scene edge"
    )
    print(f"  surviving interior-land candidates: {int(candidates.sum()):,}")

    # No-data neighbourhood → coastal / scene-edge proximity.
    ever_observed = months_observed > 0
    coastal = np.zeros_like(persistence)
    rows, cols = np.nonzero(candidates)
    for r, c in zip(rows, cols):
        r0, r1 = max(0, r - COAST_WINDOW), min(ever_observed.shape[0], r + COAST_WINDOW + 1)
        c0, c1 = max(0, c - COAST_WINDOW), min(ever_observed.shape[1], c + COAST_WINDOW + 1)
        window = ever_observed[r0:r1, c0:c1]
        coastal[r, c] = 1.0 - window.mean()

    # Greedy clustering, strongest first.
    order = sorted(zip(rows, cols), key=lambda rc: -mean_anom[rc[0], rc[1]])
    hotspots: list[Hotspot] = []
    for r, c in order:
        lon = LON_MIN + (c + 0.5) * CELL
        lat = LAT_MAX - (r + 0.5) * CELL
        if any(
            abs(lon - h.lon) < CLUSTER_RADIUS_DEG and abs(lat - h.lat) < CLUSTER_RADIUS_DEG
            for h in hotspots
        ):
            continue
        hotspots.append(
            Hotspot(
                lon=round(lon, 3),
                lat=round(lat, 3),
                months_observed=int(months_observed[r, c]),
                months_elevated=int(months_elevated[r, c]),
                mean_anomaly_ppb=round(float(mean_anom[r, c]), 1),
                peak_anomaly_ppb=round(float(peak_anom[r, c]), 1),
                mean_obs_per_month=round(float(mean_obs[r, c]), 1),
                coastal_fraction=round(float(coastal[r, c]), 2),
            )
        )

    infra = _load_infrastructure(data_dir)
    print(f"matching against {len(infra):,} infrastructure features")
    for h in hotspots:
        h.nearest = _nearest_infrastructure(h.lon, h.lat, infra)

    result = {
        "generated_from": {"periods": labels, "kind": kind},
        "method": {
            "elevated_threshold_ppb": ELEVATED_PPB,
            "min_months_observed": MIN_MONTHS_OBSERVED,
            "min_persistence": MIN_PERSISTENCE,
            "cluster_radius_deg": CLUSTER_RADIUS_DEG,
            "coast_buffer_cells": COAST_BUFFER_CELLS,
            "cells_rejected_as_coastal": rejected_coastal,
            "anomaly_sd_ppb": round(float(pooled.std()), 1),
            "anomaly_p99_ppb": round(float(np.percentile(pooled, 99)), 1),
            "note": (
                "Nearest infrastructure is proximity only, not attribution. TROPOMI's "
                "~7 km footprint cannot resolve individual facilities. Cells within "
                "the coast buffer are excluded: without that filter the persistence "
                "test surfaced a shoreline-wide artifact, because a footprint "
                "straddling the coast is biased systematically and therefore "
                "persistently."
            ),
        },
        "hotspots": [
            {
                "lon": h.lon,
                "lat": h.lat,
                "months_observed": h.months_observed,
                "months_elevated": h.months_elevated,
                "persistence": round(h.persistence, 2),
                "mean_anomaly_ppb": h.mean_anomaly_ppb,
                "peak_anomaly_ppb": h.peak_anomaly_ppb,
                "mean_obs_per_month": h.mean_obs_per_month,
                "coastal_fraction": h.coastal_fraction,
                "nearest_infrastructure": h.nearest,
            }
            for h in hotspots
        ],
    }
    return result


def run(raster_dir: Path, data_dir: Path, out_path: Path, kind: str = "month") -> dict[str, Any]:
    result = find(raster_dir, data_dir, kind=kind)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")

    spots = result["hotspots"]
    print(f"\n{len(spots)} persistent hotspot(s) -> {out_path}\n")
    for i, h in enumerate(spots[:15], 1):
        near = h["nearest_infrastructure"][0] if h["nearest_infrastructure"] else None
        tag = f"{near['name']} ({near['layer'].replace('_', ' ')}, {near['distance_km']} km)" if near else "nothing mapped nearby"
        coast = " [coastal]" if h["coastal_fraction"] > 0.4 else ""
        print(
            f"{i:2d}. {h['lat']:7.2f},{h['lon']:7.2f}  "
            f"+{h['mean_anomaly_ppb']:5.1f} ppb mean, peak +{h['peak_anomaly_ppb']:5.1f}  "
            f"{h['months_elevated']}/{h['months_observed']} months{coast}\n"
            f"      nearest: {tag}"
        )
    return result
