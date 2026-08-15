"""Has anyone actually looked at PNG? Reading the EMIT archive to find out.

No provider publishes a single methane plume over Papua New Guinea, and TROPOMI
cannot see it. The obvious reading is that PNG is clean. This module tests that
reading against the one dataset that can settle it.

EMIT has already imaged the PNG LNG chain many times — the data is public and
sitting in the archive unprocessed. Each granule ships three rasters:

  CH4ENH     methane enhancement, ppm-m
  CH4SENS    per-pixel detection sensitivity
  CH4UNCERT  retrieval uncertainty

Enhancement alone cannot separate "nothing was emitting" from "we could not
have seen it if it were". Cloud, swath edges and low sensitivity all produce the
same empty result. So this counts, per facility and per overpass:

  * did the granule footprint even contain the site
  * were there valid (non-cloud, in-swath) pixels there
  * what enhancement was present
  * what sensitivity applied, i.e. what could have been detected

The output is an observability record, and it is a publishable result either
way: either PNG is genuinely quiet to a stated detection floor, or the world's
assumption of a clean PNG rests on almost no usable observations.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import numpy as np

from . import config

CMR = "https://cmr.earthdata.nasa.gov/search/granules.umm_json"
ENH_COLLECTION = "C3242680113-LPCLOUD"  # EMITL2BCH4ENH v002
UA = {"User-Agent": "methane-atlas/0.1 (open public-good project)"}

# Radius sampled around a facility, in 60 m EMIT pixels. 12 px is ~720 m, which
# covers a plant footprint and the near field where a plume would still be
# compact.
SITE_RADIUS_PX = 12

# A site counts as observed only if this fraction of the sample box carries
# valid retrievals — a couple of stray pixels at a swath edge prove nothing.
MIN_VALID_FRACTION = 0.25

# EMIT plume complexes are typically flagged well above this; used only to
# describe what the observed sensitivity would have permitted.
NOTABLE_ENHANCEMENT_PPMM = 200.0


@dataclass
class Overpass:
    granule: str
    datetime_utc: str
    contains_site: bool
    valid_fraction: float
    observed: bool
    site_median: float | None = None
    site_max: float | None = None
    scene_median: float | None = None
    scene_sigma: float | None = None
    excess_sigma: float | None = None
    pixels_above_3sigma: int | None = None
    median_sensitivity: float | None = None
    notes: list[str] = field(default_factory=list)


def _gdal_auth_env(token: str) -> None:
    """Let GDAL read the protected LP DAAC objects over /vsicurl/ so only the
    needed bytes are fetched instead of whole 5 MB rasters."""
    os.environ.update(
        {
            "GDAL_HTTP_HEADERS": f"Authorization: Bearer {token}",
            "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
            "CPL_VSIL_CURL_USE_HEAD": "NO",
            "GDAL_HTTP_MAX_RETRY": "3",
            "GDAL_HTTP_RETRY_DELAY": "2",
        }
    )


def search_enhancement_granules(lon: float, lat: float, box_deg: float = 0.06) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    with httpx.Client(headers=UA, timeout=180, follow_redirects=True) as c:
        page = 1
        while True:
            r = c.get(
                CMR,
                params={
                    "collection_concept_id": ENH_COLLECTION,
                    "bounding_box": f"{lon-box_deg},{lat-box_deg},{lon+box_deg},{lat+box_deg}",
                    "page_size": 200,
                    "page_num": page,
                },
            )
            r.raise_for_status()
            items = r.json().get("items", [])
            for it in items:
                umm = it["umm"]
                urls = {}
                for rel in umm.get("RelatedUrls", []) or []:
                    u = rel.get("URL", "")
                    if not u.startswith("https") or not u.endswith(".tif"):
                        continue
                    # Match the FILENAME, never the whole URL: every asset sits
                    # under a directory named EMITL2BCH4ENH.002/, so testing the
                    # URL puts "CH4ENH" in all of them. Doing that silently
                    # assigned the sensitivity and then the uncertainty raster
                    # to the enhancement slot, each overwriting the last.
                    fname = u.rsplit("/", 1)[-1]
                    if "_CH4ENH_" in fname:
                        urls["enh"] = u
                    elif "_CH4SENS_" in fname:
                        urls["sens"] = u
                    elif "_CH4UNCERT_" in fname:
                        urls["uncert"] = u
                if "enh" not in urls:
                    continue
                temporal = umm.get("TemporalExtent", {})
                out.append(
                    {
                        "granule": umm["GranuleUR"],
                        "datetime": temporal.get("SingleDateTime")
                        or temporal.get("RangeDateTime", {}).get("BeginningDateTime", ""),
                        **urls,
                    }
                )
            if len(items) < 200:
                break
            page += 1
    return out


def _sample(
    uri: str, lon: float, lat: float, radius_px: int = SITE_RADIUS_PX
) -> tuple[np.ndarray | None, bool]:
    """Window-read around a point. Returns (values, site_inside_footprint)."""
    import rasterio
    from rasterio.windows import Window

    with rasterio.open(uri) as ds:
        b = ds.bounds
        if not (b.left <= lon <= b.right and b.bottom <= lat <= b.top):
            return None, False
        row, col = ds.index(lon, lat)
        r0 = max(0, row - radius_px)
        c0 = max(0, col - radius_px)
        r1 = min(ds.height, row + radius_px)
        c1 = min(ds.width, col + radius_px)
        if r1 - r0 < 4 or c1 - c0 < 4:
            return None, True
        arr = ds.read(1, window=Window(c0, r0, c1 - c0, r1 - r0)).astype("float64")
        if ds.nodata is not None:
            arr = np.where(arr == ds.nodata, np.nan, arr)
        return arr, True


def _scene_background(uri: str, lon: float, lat: float) -> tuple[float, float] | None:
    """Median and robust sigma of enhancement over a wide box around the site.

    The retrieval's own noise is large — scene-wide values span roughly ±1800
    ppm-m — so an absolute maximum inside a small box says nothing. What matters
    is how far the site sits above the surrounding scene, measured in that
    scene's own noise.
    """
    wide, inside = _sample(uri, lon, lat, radius_px=SITE_RADIUS_PX * 12)
    if not inside or wide is None:
        return None
    f = wide[np.isfinite(wide)]
    if f.size < 2000:
        return None
    med = float(np.median(f))
    sigma = float(1.4826 * np.median(np.abs(f - med)))
    return med, sigma


def scan_site(name: str, lon: float, lat: float, limit: int | None = None, verbose: bool = True):
    granules = search_enhancement_granules(lon, lat)
    granules.sort(key=lambda g: g["datetime"])
    if limit:
        granules = granules[:limit]
    if verbose:
        print(f"\n{name}  ({lat:.4f}, {lon:.4f})")
        print(f"  {len(granules)} EMIT methane granules whose search box contains the site")

    passes: list[Overpass] = []
    for g in granules:
        try:
            enh, inside = _sample(g["enh"], lon, lat)
        except Exception as e:
            passes.append(
                Overpass(g["granule"], g["datetime"][:19], False, 0.0, False,
                         notes=[f"read failed: {type(e).__name__}"])
            )
            continue
        if not inside or enh is None:
            passes.append(Overpass(g["granule"], g["datetime"][:19], inside, 0.0, False))
            continue

        finite = enh[np.isfinite(enh)]
        frac = finite.size / enh.size if enh.size else 0.0
        observed = frac >= MIN_VALID_FRACTION
        op = Overpass(g["granule"], g["datetime"][:19], True, round(frac, 3), observed)

        if finite.size:
            op.site_median = round(float(np.median(finite)), 1)
            op.site_max = round(float(finite.max()), 1)

        if observed:
            background = _scene_background(g["enh"], lon, lat)
            if background:
                med, sigma = background
                op.scene_median = round(med, 1)
                op.scene_sigma = round(sigma, 1)
                if sigma > 0:
                    op.excess_sigma = round((op.site_median - med) / sigma, 2)
                    op.pixels_above_3sigma = int((finite > med + 3 * sigma).sum())
            if g.get("sens"):
                try:
                    sens, _ = _sample(g["sens"], lon, lat)
                    if sens is not None:
                        sf = sens[np.isfinite(sens)]
                        if sf.size:
                            op.median_sensitivity = round(float(np.median(sf)), 3)
                except Exception:
                    op.notes.append("sensitivity unreadable")

        passes.append(op)
        if verbose:
            state = "observed" if observed else "obscured"
            extra = ""
            if observed and op.excess_sigma is not None:
                extra = (
                    f"  site {op.site_median:+7.1f} vs scene {op.scene_median:+7.1f}"
                    f" (±{op.scene_sigma:.0f})  = {op.excess_sigma:+5.2f}σ"
                    f"  hot px {op.pixels_above_3sigma}"
                )
            print(f"    {op.datetime_utc[:10]}  {state:8s}  valid {100*frac:5.1f}%{extra}")

    contained = [p for p in passes if p.contains_site]
    observed = [p for p in passes if p.observed]
    stats = {
        "site": name,
        "lon": lon,
        "lat": lat,
        "granules_matching_search": len(granules),
        "granules_containing_site": len(contained),
        "usable_overpasses": len(observed),
        "observability_rate": round(len(observed) / len(contained), 3) if contained else 0.0,
        "first_usable": min((p.datetime_utc for p in observed), default=None),
        "last_usable": max((p.datetime_utc for p in observed), default=None),
        "strongest_excess_sigma": max(
            (p.excess_sigma for p in observed if p.excess_sigma is not None), default=None
        ),
        "max_site_enhancement_ppmm": max(
            (p.site_max for p in observed if p.site_max is not None), default=None
        ),
        "median_sensitivity": (
            round(float(np.median([p.median_sensitivity for p in observed if p.median_sensitivity is not None])), 3)
            if any(p.median_sensitivity is not None for p in observed)
            else None
        ),
    }
    if verbose:
        print(
            f"  -> {len(observed)} usable of {len(contained)} containing the site "
            f"({100*stats['observability_rate']:.0f}% observability)"
        )
    return passes, stats


def run(sites: dict[str, tuple[float, float]], out_path: Path, limit: int | None = None) -> dict[str, Any]:
    config.load()
    token = config.get("EARTHDATA_TOKEN")
    if not token:
        raise SystemExit("EARTHDATA_TOKEN required — see docs/ACCOUNTS_PHASE2.md")
    _gdal_auth_env(token)

    print("EMIT methane archive scan — has PNG actually been observed?")
    all_stats = []
    detail: dict[str, Any] = {}
    for name, (lon, lat) in sites.items():
        passes, stats = scan_site(name, lon, lat, limit=limit)
        all_stats.append(stats)
        detail[name] = [p.__dict__ for p in passes]

    total_containing = sum(s["granules_containing_site"] for s in all_stats)
    total_usable = sum(s["usable_overpasses"] for s in all_stats)
    summary = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "instrument": "NASA EMIT (EMITL2BCH4ENH v002)",
        "method": (
            f"Sampled a {2*SITE_RADIUS_PX}x{2*SITE_RADIUS_PX} pixel box (~{2*SITE_RADIUS_PX*60} m) "
            f"at each facility across every archived granule containing it. An overpass counts as "
            f"usable only when at least {MIN_VALID_FRACTION:.0%} of that box carries valid retrievals."
        ),
        "sites": all_stats,
        "totals": {
            "granules_containing_a_site": total_containing,
            "usable_overpasses": total_usable,
            "observability_rate": round(total_usable / total_containing, 3) if total_containing else 0.0,
        },
        "interpretation_note": (
            "Enhancement alone cannot distinguish 'nothing emitting' from 'not observable'. "
            "Cloud, swath edges and low sensitivity all yield an empty result, so the "
            "observability rate is the number that determines what the absence of published "
            "PNG plumes actually means."
        ),
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({"summary": summary, "overpasses": detail}, indent=2), encoding="utf-8")

    print("\n" + "=" * 72)
    print(
        f"{total_usable} usable overpasses from {total_containing} that contained a site "
        f"({100*summary['totals']['observability_rate']:.0f}%)"
    )
    print(f"-> {out_path}")
    return summary
