"""Sentinel-5P TROPOMI methane composites.

Pipeline per period: enumerate granules over the ROI via the anonymous OData
catalogue, pull each one from CDSE's S3 store, bin its qa-filtered pixels onto
a regular grid, then delete the granule. Only the composite persists, so peak
disk stays in the hundreds of MB regardless of how much history is processed.

Outputs per period, as a 3-band COG:
  1 xch4_mean    mean column methane (ppb)
  2 xch4_anom    mean minus the region's own median for that period (ppb)
  3 valid_obs    count of qa-passing retrievals per cell (the honesty band)

Verified against a real granule 2026-08-14: variables live under the PRODUCT
group as latitude / longitude / qa_value / methane_mixing_ratio_bias_corrected
(with a _destriped variant preferred when present).
"""

from __future__ import annotations

import concurrent.futures
import datetime as dt
import json
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import httpx
import numpy as np

from . import ROI, config

ODATA = "https://catalogue.dataspace.copernicus.eu/odata/v1/Products"
S3_ENDPOINT = "https://eodata.dataspace.copernicus.eu"
S3_BUCKET = "eodata"
UA = {"User-Agent": "methane-atlas/0.1 (open public-good project)"}

# 0.05° grid over the ROI: 1040 x 920 cells.
CELL = 0.05
LON_MIN, LAT_MIN, LON_MAX, LAT_MAX = ROI
NX = int(round((LON_MAX - LON_MIN) / CELL))
NY = int(round((LAT_MAX - LAT_MIN) / CELL))

QA_MIN = 0.5  # ESA's recommended threshold for usable CH4 retrievals
MAX_CONNECTIONS = 4  # CDSE hard limit on concurrent S3 connections

# A single retrieval's noise is comparable to the enhancement we're looking for,
# so the anomaly band is only published where a cell has at least this many
# qa-passing observations. The mean and count bands stay unfiltered.
MIN_OBS_FOR_ANOMALY = 3

# Half-height of the latitude window used to estimate background, in grid rows
# (20 rows = 1.0°). XCH4 has a strong latitudinal gradient across this ROI's 46°
# span, so a single region-wide median would read that gradient as enhancement.
BACKGROUND_HALF_WINDOW = 20

PREFERRED_VARS = (
    "methane_mixing_ratio_bias_corrected_destriped",
    "methane_mixing_ratio_bias_corrected",
    "methane_mixing_ratio",
)


@dataclass(frozen=True)
class Granule:
    id: str
    name: str
    s3_key: str
    size_bytes: int
    start: str

    @property
    def size_mb(self) -> float:
        return self.size_bytes / 1e6


def _roi_polygon() -> str:
    return (
        f"POLYGON(({LON_MIN} {LAT_MIN},{LON_MAX} {LAT_MIN},"
        f"{LON_MAX} {LAT_MAX},{LON_MIN} {LAT_MAX},{LON_MIN} {LAT_MIN}))"
    )


def search_granules(start: dt.date, end: dt.date, stream: str = "OFFL") -> list[Granule]:
    """Enumerate CH4 granules intersecting the ROI. Anonymous — no credentials."""
    flt = (
        f"contains(Name,'S5P_{stream}_L2__CH4') "
        f"and ContentDate/Start gt {start.isoformat()}T00:00:00.000Z "
        f"and ContentDate/Start lt {end.isoformat()}T00:00:00.000Z "
        f"and OData.CSC.Intersects(area=geography'SRID=4326;{_roi_polygon()}')"
    )
    out: list[Granule] = []
    page = 500
    with httpx.Client(headers=UA, timeout=120, follow_redirects=True) as client:
        skip = 0
        while True:
            r = client.get(
                ODATA,
                params={
                    "$filter": flt,
                    "$orderby": "ContentDate/Start asc",
                    "$top": str(page),
                    "$skip": str(skip),
                },
            )
            r.raise_for_status()
            items = r.json().get("value", [])
            for it in items:
                key = (it.get("S3Path") or "").lstrip("/")
                if key.startswith(f"{S3_BUCKET}/"):
                    key = key[len(S3_BUCKET) + 1 :]
                if not key:
                    continue
                out.append(
                    Granule(
                        id=it["Id"],
                        name=it["Name"],
                        s3_key=key,
                        size_bytes=int(it.get("ContentLength") or 0),
                        start=(it.get("ContentDate") or {}).get("Start", "")[:19],
                    )
                )
            if len(items) < page:
                return out
            skip += len(items)


def _s3_client():
    import boto3
    from botocore.client import Config as BotoConfig

    return boto3.client(
        "s3",
        endpoint_url=S3_ENDPOINT,
        aws_access_key_id=config.get("CDSE_S3_ACCESS_KEY"),
        aws_secret_access_key=config.get("CDSE_S3_SECRET_KEY"),
        region_name="default",
        config=BotoConfig(
            signature_version="s3v4",
            max_pool_connections=MAX_CONNECTIONS,
            retries={"max_attempts": 3},
        ),
    )


def read_granule(path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return (lons, lats, xch4_ppb) for qa-passing pixels inside the ROI."""
    import netCDF4

    with netCDF4.Dataset(path) as ds:
        product = ds.groups["PRODUCT"]
        var_name = next((v for v in PREFERRED_VARS if v in product.variables), None)
        if var_name is None:
            raise ValueError(f"{path.name}: no methane variable found")

        lat = np.ravel(product.variables["latitude"][:])
        lon = np.ravel(product.variables["longitude"][:])
        qa = np.ravel(product.variables["qa_value"][:])
        val = np.ravel(product.variables[var_name][:])

    # netCDF4 hands back masked arrays; treat masked entries as invalid.
    def unmask(a: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        if np.ma.isMaskedArray(a):
            return a.filled(np.nan).astype("float64"), ~np.ma.getmaskarray(a)
        return a.astype("float64"), np.ones(a.shape, dtype=bool)

    lat, m1 = unmask(lat)
    lon, m2 = unmask(lon)
    qa, m3 = unmask(qa)
    val, m4 = unmask(val)

    keep = (
        m1
        & m2
        & m3
        & m4
        & np.isfinite(lat)
        & np.isfinite(lon)
        & np.isfinite(val)
        & (qa >= QA_MIN)
        & (lon >= LON_MIN)
        & (lon < LON_MAX)
        & (lat >= LAT_MIN)
        & (lat < LAT_MAX)
    )
    return lon[keep], lat[keep], val[keep]


def accumulate(
    lons: np.ndarray, lats: np.ndarray, vals: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Bin pixel centres onto the grid, returning (sum, count) arrays.

    Row 0 is the northern edge so the array is already in raster orientation.
    """
    col = ((lons - LON_MIN) / CELL).astype(np.int64)
    row = ((LAT_MAX - lats) / CELL).astype(np.int64)
    np.clip(col, 0, NX - 1, out=col)
    np.clip(row, 0, NY - 1, out=row)
    flat = row * NX + col

    total = np.bincount(flat, weights=vals, minlength=NX * NY).reshape(NY, NX)
    count = np.bincount(flat, minlength=NX * NY).reshape(NY, NX).astype("float64")
    return total, count


def _fetch_and_bin(granule: Granule, workdir: Path) -> tuple[np.ndarray, np.ndarray] | None:
    """Download one granule, bin it, delete it. Returns (sum, count) or None."""
    s3 = _s3_client()
    dest = workdir / granule.name
    try:
        s3.download_file(S3_BUCKET, granule.s3_key, str(dest))
        lons, lats, vals = read_granule(dest)
        if lons.size == 0:
            return None
        return accumulate(lons, lats, vals)
    except Exception as e:  # one bad granule must not sink the period
        print(f"    ! skipped {granule.name}: {type(e).__name__}: {str(e)[:90]}")
        return None
    finally:
        dest.unlink(missing_ok=True)


def write_cog(path: Path, mean: np.ndarray, anom: np.ndarray, count: np.ndarray) -> None:
    import rasterio
    from rasterio.transform import from_origin

    path.parent.mkdir(parents=True, exist_ok=True)
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
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(mean.astype("float32"), 1)
        dst.write(anom.astype("float32"), 2)
        dst.write(count.astype("float32"), 3)
        dst.set_band_description(1, "xch4_mean_ppb")
        dst.set_band_description(2, "xch4_anomaly_ppb")
        dst.set_band_description(3, "valid_obs_count")


def latitude_background(mean: np.ndarray) -> np.ndarray:
    """Background XCH4 as a function of latitude: a per-row median taken over a
    sliding latitude window, so the anomaly measures local enhancement rather
    than the atmosphere's north-south gradient.

    Returns a column vector broadcastable against the grid. Rows with too little
    coverage fall back to the whole-region median.
    """
    global_median = float(np.nanmedian(mean)) if np.isfinite(mean).any() else np.nan
    background = np.full((NY, 1), global_median, dtype="float64")
    for row in range(NY):
        lo = max(0, row - BACKGROUND_HALF_WINDOW)
        hi = min(NY, row + BACKGROUND_HALF_WINDOW + 1)
        window = mean[lo:hi, :]
        finite = window[np.isfinite(window)]
        if finite.size >= 50:
            background[row, 0] = np.median(finite)
    return background


def periods(kind: str, start: dt.date, end: dt.date) -> Iterator[tuple[str, dt.date, dt.date]]:
    """Yield (label, period_start, period_end) covering [start, end)."""
    if kind == "month":
        cursor = start.replace(day=1)
        while cursor < end:
            nxt = (cursor.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
            yield f"{cursor.year:04d}-{cursor.month:02d}", cursor, min(nxt, end)
            cursor = nxt
    elif kind == "week":
        cursor = start - dt.timedelta(days=start.weekday())  # back to Monday
        while cursor < end:
            nxt = cursor + dt.timedelta(days=7)
            iso = cursor.isocalendar()
            yield f"{iso.year:04d}-W{iso.week:02d}", cursor, min(nxt, end)
            cursor = nxt
    else:
        raise ValueError(f"unknown period kind: {kind}")


def composite_period(
    label: str,
    start: dt.date,
    end: dt.date,
    out_dir: Path,
    kind: str,
    limit: int | None = None,
) -> dict | None:
    granules = search_granules(start, end)
    if limit:
        granules = granules[:limit]
    if not granules:
        print(f"  {label}: no granules")
        return None

    volume = sum(g.size_mb for g in granules)
    print(f"  {label}: {len(granules)} granules ({volume:.0f} MB to stream)")

    total = np.zeros((NY, NX), dtype="float64")
    count = np.zeros((NY, NX), dtype="float64")
    done = 0

    with tempfile.TemporaryDirectory(prefix="matlas-s5p-") as tmp:
        workdir = Path(tmp)
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_CONNECTIONS) as pool:
            futures = {pool.submit(_fetch_and_bin, g, workdir): g for g in granules}
            for fut in concurrent.futures.as_completed(futures):
                result = fut.result()
                done += 1
                if result is not None:
                    total += result[0]
                    count += result[1]
                if done % 10 == 0 or done == len(granules):
                    print(f"    {done}/{len(granules)} granules processed")

    with np.errstate(invalid="ignore", divide="ignore"):
        mean = np.where(count > 0, total / np.maximum(count, 1), np.nan)

    valid = np.isfinite(mean)
    if not valid.any():
        print(f"  {label}: no valid retrievals — not written")
        return None

    # Anomaly against a latitude-dependent background, published only where the
    # cell has enough observations for the value to mean anything.
    background = latitude_background(mean)
    well_sampled = valid & (count >= MIN_OBS_FOR_ANOMALY)
    anom = np.where(well_sampled, mean - background, np.nan)

    out_path = out_dir / kind / f"{label}.tif"
    write_cog(out_path, mean, anom, count)

    anom_pct = 100.0 * well_sampled.sum() / well_sampled.size
    stats = {
        "period": label,
        "kind": kind,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "granules": len(granules),
        "coverage_pct": round(100.0 * valid.sum() / valid.size, 2),
        "anomaly_coverage_pct": round(anom_pct, 2),
        "background_ppb": round(float(np.nanmedian(background)), 1),
        "mean_ppb": round(float(np.nanmean(mean)), 1),
        "max_ppb": round(float(np.nanmax(mean)), 1),
        "max_anomaly_ppb": (
            round(float(np.nanmax(anom)), 1) if well_sampled.any() else None
        ),
        "median_obs_per_cell": float(np.median(count[valid])) if valid.any() else 0.0,
        "obs_total": int(count.sum()),
        "min_obs_for_anomaly": MIN_OBS_FOR_ANOMALY,
        "qa_min": QA_MIN,
        "file": str(out_path.relative_to(out_dir)).replace("\\", "/"),
    }
    print(
        f"  {label}: coverage {stats['coverage_pct']}%"
        f" · anomaly-grade {stats['anomaly_coverage_pct']}%"
        f" · background {stats['background_ppb']} ppb"
        f" · peak anomaly {'+' + str(stats['max_anomaly_ppb']) if stats['max_anomaly_ppb'] else 'n/a'} ppb"
    )
    return stats


def run(
    out_dir: Path,
    start: dt.date,
    end: dt.date,
    kind: str = "week",
    limit: int | None = None,
) -> list[dict]:
    config.load()
    if not config.CDSE_S3.ready:
        raise SystemExit(
            "CDSE S3 keys missing. Set CDSE_S3_ACCESS_KEY and CDSE_S3_SECRET_KEY "
            "in .env — see docs/CREDENTIALS.md"
        )
    out_dir.mkdir(parents=True, exist_ok=True)
    index_path = out_dir / f"{kind}_index.json"
    existing = {}
    if index_path.exists():
        existing = {e["period"]: e for e in json.loads(index_path.read_text())}

    print(f"TROPOMI {kind}ly composites {start} -> {end}  (grid {NX}x{NY} @ {CELL}°)")
    results = list(existing.values())
    for label, p_start, p_end in periods(kind, start, end):
        if label in existing and (out_dir / existing[label]["file"]).exists():
            print(f"  {label}: already done, skipping")
            continue
        stats = composite_period(label, p_start, p_end, out_dir, kind, limit=limit)
        if stats:
            results = [r for r in results if r["period"] != label] + [stats]
            results.sort(key=lambda r: r["period"])
            # Write after every period so an interrupted run stays resumable.
            index_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\n{len(results)} periods in {index_path}")
    return results
