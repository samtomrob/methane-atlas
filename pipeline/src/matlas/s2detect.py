"""Our own methane plume detection from Sentinel-2, starting with PNG.

Every plume elsewhere in this project was found by someone else. This module
detects them from raw imagery, which matters most over Papua New Guinea: all
four plume providers publish *zero* detections there and TROPOMI cannot see it
(0.2% usable coverage), yet Sentinel-2 images the Hides gas field roughly every
three days. PNG is unexamined rather than unobservable.

Method — multi-band multi-pass (MBMP), after Varon et al. (2021):

  Methane absorbs strongly in Sentinel-2's B12 (~2190 nm) and only weakly in
  B11 (~1610 nm), so the ratio R = B12/B11 dips where a plume sits. R also
  varies with whatever is on the ground, so a single scene is dominated by
  surface texture rather than gas. Differencing R against a reference built
  from other passes over the same ground cancels the static surface and leaves
  transient absorbers:

      dR = (R_target - R_reference) / R_reference        (negative = absorption)

  A real plume is a spatially coherent patch of negative dR anchored near its
  source, so detection thresholds on robust statistics and then demands
  contiguity, which rejects isolated pixel noise.

Honest limits, stated because they shape what this can find:

  * Sensitivity scales with SWIR surface brightness. Rainforest is dark and
    heterogeneous — close to the worst case. The cleared industrial pads at
    these facilities are bright, which is precisely where a leak would start,
    so AOIs are drawn tightly around infrastructure rather than across forest.
  * Sentinel-2's practical floor is around 1-3 t/hr over favourable ground and
    worse over dark ground. Small leaks will be invisible.
  * Detections here are candidates for review, never confirmed emissions.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import numpy as np

from . import config

ODATA = "https://catalogue.dataspace.copernicus.eu/odata/v1/Products"
UA = {"User-Agent": "methane-atlas/0.1 (open public-good project)"}

# Sentinel-2 Scene Classification values we refuse to use.
SCL_REJECT = {
    0,  # no data
    1,  # saturated / defective
    3,  # cloud shadow
    6,  # water
    8,  # cloud medium probability
    9,  # cloud high probability
    10,  # thin cirrus
    11,  # snow / ice
}

# Detection thresholds. k is in robust sigmas below the local median; a plume
# must also cover at least MIN_PLUME_PIXELS contiguous 20 m cells (~0.5 ha),
# which is what separates a plume from speckle.
SIGMA_K = 4.0
MIN_PLUME_PIXELS = 12
MIN_REFERENCE_SCENES = 3

# --- Discriminators, all added after measurement ---
#
# A first pass over Hides returned 95 "detections" from 8 scenes, of which 25 of
# 44 distinct locations recurred on up to four separate dates — the two
# strongest sat at an identical position two days apart. Those are permanent
# ground features. Three filters remove that class of error:
#
# 1. Physical. Methane absorbs strongly at B12 (~2190 nm) and barely at all at
#    B11 (~1610 nm), so its signature is specific: B12 darkens while B11 stays
#    put. Requiring only that B12 fall *relative* to B11 was too weak — it
#    admitted anomalies where B11 had brightened 5-9%, which is a surface
#    moisture change (B11 tracks water content), not gas. Both conditions are
#    now enforced.
B12_MIN_ABSORPTION = 0.02  # dB12 must be at least this far below zero
B11_MAX_CHANGE = 0.03  # and dB11 must stay within this of zero
#
# 2. Transience. A plume disperses within minutes. Anything flagged at the same
#    spot in more than one scene is, by definition, not a plume.
RECURRENCE_RADIUS_KM = 0.25
#
# 3. Scene sanity. Residual cloud edge, haze or an illumination shift produces
#    anomalies across a whole scene at once. A scene yielding more than this
#    many clusters is contaminated and is discarded entirely rather than
#    contributing its least-bad candidates.
#
#    Counted on RAW clusters, before the physical gate. Counting survivors
#    instead made the two filters fight each other: tightening the physics
#    dropped per-scene survivor counts below the limit, which quietly
#    readmitted contaminated scenes and *raised* the candidate count from 5 to
#    15 at Hides. Contamination is a property of the scene, not of how many
#    clusters happen to survive a later test.
MAX_RAW_CLUSTERS_PER_SCENE = 15

# Scenes cloudier than this are not worth opening.
MAX_CLOUD_PCT = 60.0


@dataclass(frozen=True)
class Site:
    """A detection target, drawn tight around cleared industrial ground."""

    key: str
    name: str
    lon: float
    lat: float
    half_km: float = 5.0
    note: str = ""

    def bbox(self) -> tuple[float, float, float, float]:
        dlat = self.half_km / 110.57
        dlon = self.half_km / (111.32 * np.cos(np.radians(self.lat)))
        return self.lon - dlon, self.lat - dlat, self.lon + dlon, self.lat + dlat


# The PNG LNG / Papua LNG chain. Coordinates sit on the GEM pipeline routes
# already in this project's infrastructure layer.
PNG_SITES: tuple[Site, ...] = (
    Site("hides", "Hides Gas Conditioning Plant", 142.87, -5.90, 6.0, "upstream conditioning, Hela"),
    Site("angore", "Angore wellpads", 142.99, -5.96, 5.0, "tie-in to Hides"),
    Site("kutubu", "Kutubu Central Processing Facility", 143.32, -6.41, 5.0, "oil CPF, associated gas"),
    Site("gobe", "Gobe processing facility", 143.55, -6.78, 4.0, ""),
    Site("cautionbay", "PNG LNG plant, Caution Bay", 146.87, -9.16, 6.0, "liquefaction and export"),
    Site("elk", "Elk-Antelope (Papua LNG)", 145.35, -7.55, 5.0, "development area"),
)

# Australian coal mines, chosen because other providers have already detected
# plumes there — they are validation targets, not guesses. Counts are plumes
# associated with each mine in this project's own aggregated layer. These sit on
# drier, brighter ground than PNG, which is where Sentinel-2 methane retrieval
# is known to work best.
AU_SITES: tuple[Site, ...] = (
    Site("grosvenor", "Grosvenor mine, Bowen Basin", 147.9927, -21.8732, 6.0, "56 known plumes"),
    Site("aquila", "Aquila-Capcoal, Bowen Basin", 148.5555, -22.9285, 6.0, "34 known plumes"),
    Site("ashton", "Ashton mine, Hunter Valley", 151.0790, -32.4667, 5.0, "37 known plumes"),
    Site("hvo", "Hunter Valley Operations", 150.9988, -32.5260, 6.0, "24 known plumes"),
    Site("appin", "Appin colliery, Sydney Basin", 150.7932, -34.2113, 5.0, "35 known plumes"),
    Site("mandalong", "Mandalong mine, NSW", 151.4622, -33.1177, 5.0, "39 known plumes"),
)

# Deliberately away from any mapped infrastructure: whatever the detector finds
# here is its false-positive floor.
CONTROL_SITES: tuple[Site, ...] = (
    Site("ctrl_png", "CONTROL — PNG forest ridge", 143.05, -5.75, 6.0, "no infrastructure"),
    Site("ctrl_qld", "CONTROL — QLD rangeland", 147.30, -22.40, 6.0, "no infrastructure"),
    Site("ctrl_nsw", "CONTROL — NSW bushland", 150.30, -32.90, 5.0, "no infrastructure"),
)

SITES: dict[str, tuple[Site, ...]] = {
    "png": PNG_SITES,
    "au": AU_SITES,
    "control": CONTROL_SITES,
}


@dataclass
class Detection:
    site: str
    site_name: str
    lon: float
    lat: float
    scene: str
    datetime_utc: str
    pixels: int
    area_ha: float
    mean_dr: float
    min_dr: float
    sigma: float
    strength: float
    reference_scenes: int
    mean_d_b11: float = 0.0
    mean_d_b12: float = 0.0
    elongation: float = 1.0
    notes: list[str] = field(default_factory=list)


def _gdal_env() -> None:
    """Point GDAL's /vsis3/ at the Copernicus object store."""
    os.environ.update(
        {
            "AWS_ACCESS_KEY_ID": config.get("CDSE_S3_ACCESS_KEY") or "",
            "AWS_SECRET_ACCESS_KEY": config.get("CDSE_S3_SECRET_KEY") or "",
            "AWS_S3_ENDPOINT": "eodata.dataspace.copernicus.eu",
            "AWS_VIRTUAL_HOSTING": "FALSE",
            "AWS_HTTPS": "YES",
            # Products hold ~95 objects; listing every directory per open is
            # pure latency.
            "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
            "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".jp2",
        }
    )


def search_scenes(site: Site, start: dt.date, end: dt.date) -> list[dict[str, Any]]:
    """L2A scenes covering the site, least cloudy first."""
    lon0, lat0, lon1, lat1 = site.bbox()
    poly = (
        f"POLYGON(({lon0} {lat0},{lon1} {lat0},{lon1} {lat1},{lon0} {lat1},{lon0} {lat0}))"
    )
    flt = (
        "Collection/Name eq 'SENTINEL-2' and contains(Name,'MSIL2A') "
        f"and ContentDate/Start gt {start.isoformat()}T00:00:00.000Z "
        f"and ContentDate/Start lt {end.isoformat()}T00:00:00.000Z "
        f"and OData.CSC.Intersects(area=geography'SRID=4326;{poly}')"
    )
    out: list[dict[str, Any]] = []
    with httpx.Client(headers=UA, timeout=180, follow_redirects=True) as client:
        skip = 0
        while True:
            r = client.get(
                ODATA,
                params={
                    "$filter": flt,
                    "$orderby": "ContentDate/Start asc",
                    "$top": "200",
                    "$skip": str(skip),
                    "$expand": "Attributes",
                },
            )
            r.raise_for_status()
            items = r.json().get("value", [])
            for it in items:
                cloud = None
                for a in it.get("Attributes", []) or []:
                    if a.get("Name") == "cloudCover":
                        cloud = a.get("Value")
                key = (it.get("S3Path") or "").lstrip("/")
                if key.startswith("eodata/"):
                    key = key[len("eodata/") :]
                out.append(
                    {
                        "name": it["Name"],
                        "key": key,
                        "start": (it.get("ContentDate") or {}).get("Start", "")[:19],
                        "cloud": float(cloud) if cloud is not None else None,
                        "tile": _tile_of(it["Name"]),
                    }
                )
            if len(items) < 200:
                break
            skip += len(items)
    return out


def _tile_of(product_name: str) -> str | None:
    for part in product_name.split("_"):
        if len(part) == 6 and part.startswith("T") and part[1:3].isdigit():
            return part
    return None


def _band_uris(s3, key: str) -> dict[str, str] | None:
    """Locate the 20 m B11, B12 and SCL objects inside a product."""
    found: dict[str, str] = {}
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket="eodata", Prefix=key):
        for o in page.get("Contents", []):
            name = o["Key"].rsplit("/", 1)[-1]
            for band in ("B11", "B12", "SCL"):
                if name.endswith(f"_{band}_20m.jp2"):
                    found[band] = f"/vsis3/eodata/{o['Key']}"
    return found if len(found) == 3 else None


def _read_window(uris: dict[str, str], site: Site) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
    """Read B11, B12 and SCL over the site footprint only."""
    import rasterio
    from rasterio.warp import transform_bounds
    from rasterio.windows import from_bounds

    lon0, lat0, lon1, lat1 = site.bbox()
    bands: dict[str, np.ndarray] = {}
    window = None
    for band in ("B11", "B12", "SCL"):
        with rasterio.open(uris[band]) as ds:
            if window is None:
                left, bottom, right, top = transform_bounds(
                    "EPSG:4326", ds.crs, lon0, lat0, lon1, lat1, densify_pts=21
                )
                window = from_bounds(left, bottom, right, top, ds.transform).round_offsets().round_lengths()
                if window.width < 24 or window.height < 24:
                    return None
                # Reject windows that fall outside the tile.
                if window.col_off < 0 or window.row_off < 0:
                    return None
                if window.col_off + window.width > ds.width or window.row_off + window.height > ds.height:
                    return None
            bands[band] = ds.read(1, window=window).astype("float64")
    return bands["B11"], bands["B12"], bands["SCL"]


def _masked_bands(
    b11: np.ndarray, b12: np.ndarray, scl: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """B11, B12 and their ratio, with unusable pixels set to NaN."""
    usable = np.isin(scl, list(SCL_REJECT), invert=True) & (b11 > 0) & (b12 > 0)
    b11m = np.where(usable, b11, np.nan)
    b12m = np.where(usable, b12, np.nan)
    with np.errstate(invalid="ignore", divide="ignore"):
        ratio = np.where(usable, b12 / b11, np.nan)
    return b11m, b12m, ratio


def _elongation(cells: np.ndarray) -> float:
    """Ratio of the cluster's principal axes. A wind-blown plume is elongated;
    a field, pond or clearing tends to be compact."""
    if len(cells) < 3:
        return 1.0
    centred = cells.astype("float64") - cells.mean(axis=0)
    cov = np.cov(centred, rowvar=False)
    eig = np.linalg.eigvalsh(cov)
    lo, hi = float(max(eig[0], 1e-9)), float(max(eig[1], 1e-9))
    return round(float(np.sqrt(hi / lo)), 2)


def _label_clusters(mask: np.ndarray, min_pixels: int) -> list[np.ndarray]:
    """Connected components (8-neighbour) via BFS — avoids a scipy dependency."""
    seen = np.zeros(mask.shape, dtype=bool)
    clusters: list[np.ndarray] = []
    h, w = mask.shape
    for r0 in range(h):
        for c0 in range(w):
            if not mask[r0, c0] or seen[r0, c0]:
                continue
            queue = deque([(r0, c0)])
            seen[r0, c0] = True
            cells: list[tuple[int, int]] = []
            while queue:
                r, c = queue.popleft()
                cells.append((r, c))
                for dr in (-1, 0, 1):
                    for dc in (-1, 0, 1):
                        rr, cc = r + dr, c + dc
                        if 0 <= rr < h and 0 <= cc < w and mask[rr, cc] and not seen[rr, cc]:
                            seen[rr, cc] = True
                            queue.append((rr, cc))
            if len(cells) >= min_pixels:
                clusters.append(np.array(cells))
    return clusters


def detect_site(
    site: Site,
    start: dt.date,
    end: dt.date,
    max_scenes: int | None = None,
    verbose: bool = True,
) -> tuple[list[Detection], dict[str, Any]]:
    import rasterio
    from rasterio.warp import transform_bounds
    from rasterio.windows import from_bounds

    from .tropomi import _s3_client

    config.load()  # safe to call directly, not only via run()
    _gdal_env()
    s3 = _s3_client()

    scenes = search_scenes(site, start, end)
    usable_scenes = [s for s in scenes if (s["cloud"] is None or s["cloud"] <= MAX_CLOUD_PCT)]
    if verbose:
        print(f"\n{site.name}")
        print(
            f"  {len(scenes)} scenes, {len(usable_scenes)} under {MAX_CLOUD_PCT:.0f}% cloud"
        )
    if len(usable_scenes) < MIN_REFERENCE_SCENES + 1:
        return [], {
            "site": site.key,
            "scenes_found": len(scenes),
            "scenes_usable": len(usable_scenes),
            "status": "too few usable scenes",
        }

    # Group by tile so the reference stack is pixel-aligned with the target.
    by_tile: dict[str, list[dict[str, Any]]] = {}
    for s in usable_scenes:
        by_tile.setdefault(s["tile"] or "?", []).append(s)
    tile, tile_scenes = max(by_tile.items(), key=lambda kv: len(kv[1]))
    if max_scenes:
        tile_scenes = sorted(tile_scenes, key=lambda s: s["cloud"] if s["cloud"] is not None else 100)[
            :max_scenes
        ]
    if verbose:
        print(f"  tile {tile}: {len(tile_scenes)} scenes")

    # Load every scene's bands once. B11 and B12 are kept separately, not just
    # their ratio, so the physical test below can be applied.
    ratios: list[tuple[dict[str, Any], np.ndarray]] = []
    b11_list: list[np.ndarray] = []
    b12_list: list[np.ndarray] = []
    swir_brightness: list[float] = []
    for s in tile_scenes:
        uris = _band_uris(s3, s["key"])
        if not uris:
            continue
        try:
            read = _read_window(uris, site)
        except Exception as e:
            if verbose:
                print(f"    ! {s['name'][:34]}: {type(e).__name__}")
            continue
        if read is None:
            continue
        b11_raw, b12_raw, scl = read
        b11, b12, r = _masked_bands(b11_raw, b12_raw, scl)
        valid_frac = float(np.isfinite(r).mean())
        if valid_frac < 0.35:
            continue
        ratios.append((s, r))
        b11_list.append(b11)
        b12_list.append(b12)
        swir_brightness.append(float(np.nanmedian(b12)))
        if verbose:
            print(
                f"    {s['start'][:10]}  cloud {s['cloud'] if s['cloud'] is not None else -1:5.1f}%  "
                f"usable pixels {100*valid_frac:5.1f}%  B12 median {np.nanmedian(b12):.0f}"
            )

    if len(ratios) < MIN_REFERENCE_SCENES + 1:
        return [], {
            "site": site.key,
            "scenes_found": len(scenes),
            "scenes_usable": len(usable_scenes),
            "scenes_read": len(ratios),
            "status": "too few cloud-free reads",
        }

    stack = np.stack([r for _, r in ratios])
    b11_stack = np.stack(b11_list)
    b12_stack = np.stack(b12_list)
    detections: list[Detection] = []
    noise_estimates: list[float] = []

    # Geo-referencing for the window, taken once from any scene.
    uris = _band_uris(s3, ratios[0][0]["key"])
    with rasterio.open(uris["B12"]) as ds:
        lon0, lat0, lon1, lat1 = site.bbox()
        left, bottom, right, top = transform_bounds(
            "EPSG:4326", ds.crs, lon0, lat0, lon1, lat1, densify_pts=21
        )
        win = from_bounds(left, bottom, right, top, ds.transform).round_offsets().round_lengths()
        win_transform = ds.window_transform(win)
        crs = ds.crs

    from rasterio.warp import transform as warp_transform

    for idx, (scene, target) in enumerate(ratios):
        others = np.delete(stack, idx, axis=0)
        with np.errstate(invalid="ignore"):
            reference = np.nanmedian(others, axis=0)
            n_ref = np.isfinite(others).sum(axis=0)
            dr = (target - reference) / reference
        dr = np.where(np.isfinite(dr) & (n_ref >= MIN_REFERENCE_SCENES), dr, np.nan)

        finite = dr[np.isfinite(dr)]
        if finite.size < 400:
            continue
        median = float(np.median(finite))
        # Median absolute deviation: robust to the plume we are looking for.
        sigma = float(1.4826 * np.median(np.abs(finite - median)))
        noise_estimates.append(sigma)
        if sigma <= 0:
            continue

        # Per-band change, for the physical test.
        with np.errstate(invalid="ignore"):
            b11_ref = np.nanmedian(np.delete(b11_stack, idx, axis=0), axis=0)
            b12_ref = np.nanmedian(np.delete(b12_stack, idx, axis=0), axis=0)
            d_b11 = (b11_stack[idx] - b11_ref) / b11_ref
            d_b12 = (b12_stack[idx] - b12_ref) / b12_ref

        threshold = median - SIGMA_K * sigma
        mask = np.isfinite(dr) & (dr < threshold)
        clusters = _label_clusters(mask, MIN_PLUME_PIXELS)

        # Judge scene contamination on the raw cluster count, before physics.
        if len(clusters) > MAX_RAW_CLUSTERS_PER_SCENE:
            if verbose:
                print(
                    f"    x {scene['start'][:10]}: {len(clusters)} raw clusters — "
                    f"scene discarded as contaminated"
                )
            continue

        scene_hits: list[Detection] = []
        for cells in clusters:
            rows, cols = cells[:, 0], cells[:, 1]
            vals = dr[rows, cols]
            mean_b11 = float(np.nanmean(d_b11[rows, cols]))
            mean_b12 = float(np.nanmean(d_b12[rows, cols]))

            # The methane signature: B12 clearly darker, B11 essentially
            # unchanged. Anything that moves B11 as well is surface, not gas.
            if mean_b12 > -B12_MIN_ABSORPTION or abs(mean_b11) > B11_MAX_CHANGE:
                continue

            cy, cx = float(rows.mean()) + 0.5, float(cols.mean()) + 0.5
            x, y = win_transform * (cx, cy)
            lon_arr, lat_arr = warp_transform(crs, "EPSG:4326", [x], [y])
            scene_hits.append(
                Detection(
                    site=site.key,
                    site_name=site.name,
                    lon=round(float(lon_arr[0]), 5),
                    lat=round(float(lat_arr[0]), 5),
                    scene=scene["name"],
                    datetime_utc=scene["start"],
                    pixels=int(len(cells)),
                    area_ha=round(len(cells) * 0.04, 2),  # 20 m pixels
                    mean_dr=round(float(vals.mean()), 5),
                    min_dr=round(float(vals.min()), 5),
                    sigma=round(sigma, 5),
                    strength=round(abs(float(vals.mean()) - median) / sigma, 1),
                    reference_scenes=int(np.median(n_ref[rows, cols])),
                    mean_d_b11=round(mean_b11, 5),
                    mean_d_b12=round(mean_b12, 5),
                    elongation=_elongation(cells),
                )
            )

        detections.extend(scene_hits)

    before_transience = len(detections)
    detections = _drop_recurring(detections)

    stats = {
        "site": site.key,
        "site_name": site.name,
        "scenes_found": len(scenes),
        "scenes_usable": len(usable_scenes),
        "scenes_read": len(ratios),
        "tile": tile,
        "median_swir_reflectance": round(float(np.median(swir_brightness)) / 10000, 4)
        if swir_brightness
        else None,
        "median_noise_sigma": round(float(np.median(noise_estimates)), 5) if noise_estimates else None,
        "clusters_passing_physical_test": before_transience,
        "rejected_as_recurring": before_transience - len(detections),
        "detections": len(detections),
        "status": "ok",
    }
    if verbose:
        s = stats["median_noise_sigma"]
        print(
            f"  analysed {len(ratios)} scenes · SWIR reflectance "
            f"{stats['median_swir_reflectance']} · noise σ {s if s is not None else 'n/a'}"
        )
        print(
            f"  {before_transience} passed the B12/B11 physical test, "
            f"{stats['rejected_as_recurring']} then rejected as recurring "
            f"→ {len(detections)} transient candidate(s)"
        )
    return detections, stats


def _drop_recurring(detections: list[Detection]) -> list[Detection]:
    """Remove anything flagged at the same place on more than one date.

    A methane plume disperses in minutes, so it cannot be imaged twice days
    apart at an identical position. Anything that is, is ground.
    """
    import math

    groups: list[dict[str, Any]] = []
    for d in detections:
        placed = False
        for g in groups:
            dx = (d.lon - g["lon"]) * 111.32 * math.cos(math.radians(d.lat))
            dy = (d.lat - g["lat"]) * 110.57
            if math.hypot(dx, dy) < RECURRENCE_RADIUS_KM:
                g["members"].append(d)
                g["dates"].add(d.datetime_utc[:10])
                placed = True
                break
        if not placed:
            groups.append(
                {"lon": d.lon, "lat": d.lat, "members": [d], "dates": {d.datetime_utc[:10]}}
            )

    kept: list[Detection] = []
    for g in groups:
        if len(g["dates"]) > 1:
            continue  # static ground feature
        kept.extend(g["members"])
    return kept


def run(
    out_path: Path,
    region: str = "png",
    start: dt.date | None = None,
    end: dt.date | None = None,
    max_scenes: int | None = 14,
    only: str | None = None,
) -> dict[str, Any]:
    config.load()
    if not config.CDSE_S3.ready:
        raise SystemExit("CDSE S3 keys required — see docs/CREDENTIALS.md")

    sites = SITES.get(region)
    if not sites:
        raise SystemExit(f"unknown region '{region}' (have: {', '.join(SITES)})")
    if only:
        sites = tuple(s for s in sites if s.key == only)
        if not sites:
            raise SystemExit(f"no site '{only}' in region '{region}'")

    end = end or dt.date.today()
    start = start or (end - dt.timedelta(days=365))
    print(f"Sentinel-2 MBMP detection · {region.upper()} · {start} -> {end}")
    print(f"threshold {SIGMA_K} sigma, min {MIN_PLUME_PIXELS} contiguous pixels (~{MIN_PLUME_PIXELS*0.04:.1f} ha)")

    all_detections: list[Detection] = []
    all_stats: list[dict[str, Any]] = []
    for site in sites:
        try:
            dets, stats = detect_site(site, start, end, max_scenes=max_scenes)
        except Exception as e:
            print(f"\n{site.name}\n  ! failed: {type(e).__name__}: {str(e)[:120]}")
            all_stats.append({"site": site.key, "status": f"error: {type(e).__name__}"})
            continue
        all_detections.extend(dets)
        all_stats.append(stats)

    features = []
    for d in sorted(all_detections, key=lambda x: -x.strength):
        props = {k: v for k, v in d.__dict__.items() if k not in ("lon", "lat") and v not in (None, [])}
        props["provider"] = "Methane Atlas (Sentinel-2 MBMP)"
        props["own_detection"] = True
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [d.lon, d.lat]},
                "properties": props,
            }
        )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps({"type": "FeatureCollection", "features": features}, separators=(",", ":")),
        encoding="utf-8",
    )

    summary = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "region": region,
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "method": "Sentinel-2 B12/B11 multi-band multi-pass (Varon et al. 2021)",
        "threshold_sigma": SIGMA_K,
        "min_pixels": MIN_PLUME_PIXELS,
        "sites": all_stats,
        "candidates": len(all_detections),
        "caveat": (
            "Candidate absorption anomalies for human review, not confirmed emissions. "
            "Sensitivity depends strongly on SWIR surface brightness; over dark tropical "
            "vegetation the effective floor is well above Sentinel-2's nominal 1-3 t/hr."
        ),
    }
    (out_path.parent / "s2_detections_status.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    print(f"\n{len(all_detections)} candidate(s) -> {out_path}")
    return summary
