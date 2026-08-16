"""Plume imagery and wind context — what a detection actually looked like.

A dot on a map says an emission happened. The concentration raster shows its
shape, extent and which way it blew, which is what makes a detection legible to
a human and checkable by a sceptic.

Carbon Mapper publishes per-plume assets and, critically, `plume_bounds`, so the
raster can be draped as a georeferenced overlay:

    plume_png   methane concentration, colourised
    rgb_png     the scene backdrop
    con_tif     concentration values in ppm-m
    plume_bounds  [west, south, east, north]

Wind comes from Open-Meteo's historical archive — free, no key — sampled at the
plume's own location and hour. It matters twice over: the plume points downwind,
so wind direction is the first check on whether a detection is physically
sensible, and emission rate scales with wind speed, so it is also the dominant
term in the uncertainty of any published figure.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import httpx

from . import config

CM = "https://api.carbonmapper.org/api/v1"
OPEN_METEO_ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
OPEN_METEO_FORECAST = "https://api.open-meteo.com/v1/forecast"
UA = {"User-Agent": "methane-atlas/0.1 (open public-good project)"}

# Assets are fetched newest-first and capped: the archive runs to thousands of
# plumes, and recency is what this view is for.
DEFAULT_ASSET_LIMIT = 200

ASSET_KINDS = ("plume_png", "rgb_png")


def _make_transparent(path: Path) -> bool:
    """Key the plume raster's black background out to transparency.

    Carbon Mapper ships these as RGB with no alpha, on pure black — 90% of the
    image. Draped as-is that is an opaque square blotting out the map. The plume
    colourmap starts at a dark purple (49,21,66), comfortably clear of black, so
    a near-black key is unambiguous. Edge pixels get a graded alpha so the plume
    fades out instead of ending on a hard cut.
    """
    import numpy as np
    import rasterio

    from .render import write_png

    try:
        with rasterio.open(path) as ds:
            if ds.count < 3:
                return False
            rgb = np.dstack([ds.read(i + 1) for i in range(3)]).astype("float64")
    except Exception:
        return False

    brightness = rgb.sum(axis=2)
    # Fully transparent at pure black, fully opaque by the time the colourmap
    # has clearly started.
    alpha = np.clip((brightness - 6.0) / 60.0, 0.0, 1.0) * 255.0

    out = np.zeros(rgb.shape[:2] + (4,), dtype="uint8")
    out[..., :3] = np.clip(rgb, 0, 255).astype("uint8")
    out[..., 3] = alpha.astype("uint8")
    write_png(path, out)
    return True


def _token(client: httpx.Client) -> str | None:
    """Login first, stored token only as fallback.

    See plumes._carbon_mapper_token: Carbon Mapper access tokens can expire the
    same day they are issued, so preferring a stored one breaks unattended runs
    within hours.
    """
    email, password = config.get("CARBON_MAPPER_EMAIL"), config.get("CARBON_MAPPER_PASSWORD")
    if email and password:
        r = client.post(f"{CM}/token/pair", json={"email": email, "password": password}, timeout=60)
        if r.status_code == 200 and r.json().get("access"):
            return r.json()["access"]
    return config.get("CARBON_MAPPER_TOKEN")


def wind_at(lat: float, lon: float, when: str, client: httpx.Client) -> dict[str, Any] | None:
    """Wind speed and direction at a plume's location and hour.

    Uses the reanalysis archive where it reaches, and the forecast API's recent
    history otherwise — the archive lags real time by several days, which is
    exactly the window the freshest detections sit in.
    """
    try:
        stamp = dt.datetime.fromisoformat(when.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    day = stamp.date()
    params = {
        "latitude": round(lat, 4),
        "longitude": round(lon, 4),
        "start_date": day.isoformat(),
        "end_date": day.isoformat(),
        "hourly": "wind_speed_10m,wind_direction_10m",
        "timezone": "UTC",
    }
    age_days = (dt.date.today() - day).days
    url = OPEN_METEO_ARCHIVE if age_days > 10 else OPEN_METEO_FORECAST
    if url == OPEN_METEO_FORECAST:
        params = {
            "latitude": params["latitude"],
            "longitude": params["longitude"],
            "hourly": params["hourly"],
            "timezone": "UTC",
            "past_days": min(92, max(1, age_days + 1)),
            "forecast_days": 1,
        }
    try:
        r = client.get(url, params=params, timeout=60)
        if r.status_code != 200:
            return None
        hourly = r.json().get("hourly", {})
    except (httpx.HTTPError, ValueError):
        return None

    times = hourly.get("time") or []
    speeds = hourly.get("wind_speed_10m") or []
    dirs = hourly.get("wind_direction_10m") or []
    if not times:
        return None

    target = stamp.strftime("%Y-%m-%dT%H:00")
    idx = times.index(target) if target in times else None
    if idx is None:
        return None
    speed_kmh, direction = speeds[idx], dirs[idx]
    if speed_kmh is None or direction is None:
        return None
    return {
        # Meteorological convention: direction is where the wind comes FROM,
        # so a plume drifts toward direction + 180.
        "wind_from_deg": round(float(direction)),
        "plume_toward_deg": round((float(direction) + 180) % 360),
        "wind_speed_ms": round(float(speed_kmh) / 3.6, 1),
        "source": "Open-Meteo",
    }


def fetch_assets(out_dir: Path, limit: int = DEFAULT_ASSET_LIMIT) -> dict[str, Any]:
    """Download plume rasters for the most recent detections."""
    from . import ROI

    lon_min, lat_min, lon_max, lat_max = ROI
    out_dir.mkdir(parents=True, exist_ok=True)
    index: dict[str, Any] = {}
    downloaded = skipped = 0

    with httpx.Client(headers=UA, timeout=180, follow_redirects=True) as client:
        token = _token(client)
        if not token:
            return {"error": "Carbon Mapper credentials required", "plumes": 0}
        auth = {"Authorization": f"Bearer {token}"}

        r = client.get(
            f"{CM}/catalog/plumes/annotated",
            params={
                "bbox": [lon_min, lat_min, lon_max, lat_max],
                "plume_gas": "CH4",
                "limit": limit,
                "sort": "published_desc",
            },
            headers=auth,
        )
        r.raise_for_status()
        items = r.json().get("items", [])

        for it in items:
            pid = it.get("plume_id")
            bounds = it.get("plume_bounds")
            if not pid or not bounds or len(bounds) != 4:
                continue
            entry: dict[str, Any] = {
                "bounds": [round(float(b), 6) for b in bounds],
                "datetime_utc": it.get("scene_timestamp"),
                "emission_kg_hr": it.get("emission_auto"),
                "assets": {},
            }
            for kind in ASSET_KINDS:
                url = it.get(kind)
                if not url:
                    continue
                dest = out_dir / f"{pid}_{kind}.png"
                if dest.exists() and dest.stat().st_size > 0:
                    entry["assets"][kind] = dest.name
                    skipped += 1
                    continue
                try:
                    a = client.get(url, headers=auth, timeout=180)
                except httpx.HTTPError:
                    continue
                if a.status_code != 200 or not a.content.startswith(b"\x89PNG"):
                    continue
                dest.write_bytes(a.content)
                # Only the concentration raster gets keyed out; the RGB scene
                # backdrop is meant to be opaque.
                if kind == "plume_png":
                    _make_transparent(dest)
                entry["assets"][kind] = dest.name
                downloaded += 1
            if entry["assets"]:
                index[pid] = entry

    (out_dir / "index.json").write_text(json.dumps(index, indent=1), encoding="utf-8")
    total_bytes = sum(f.stat().st_size for f in out_dir.glob("*.png"))
    return {
        "plumes_with_imagery": len(index),
        "files_downloaded": downloaded,
        "files_already_present": skipped,
        "total_mb": round(total_bytes / 1e6, 2),
    }


def enrich(plumes_path: Path, assets_dir: Path, limit_wind: int = 400) -> dict[str, Any]:
    """Attach imagery references and wind to the plume layer, newest first."""
    config.load()
    data = json.loads(plumes_path.read_text(encoding="utf-8"))
    feats = data.get("features", [])

    index_path = assets_dir / "index.json"
    imagery = json.loads(index_path.read_text()) if index_path.exists() else {}

    # Newest first: recency is what this view is for.
    def when(f: dict[str, Any]) -> str:
        return str(f["properties"].get("datetime_utc") or "")

    order = sorted(range(len(feats)), key=lambda i: when(feats[i]), reverse=True)

    matched = winded = 0
    with httpx.Client(headers=UA, timeout=90, follow_redirects=True) as client:
        for rank, i in enumerate(order):
            props = feats[i]["properties"]
            raw_id = str(props.get("plume_id", ""))
            pid = raw_id.split(":", 1)[1] if ":" in raw_id else raw_id
            entry = imagery.get(pid)
            if entry:
                props["imagery"] = entry["assets"]
                props["imagery_bounds"] = entry["bounds"]
                matched += 1
            if rank < limit_wind and props.get("datetime_utc"):
                lon, lat = feats[i]["geometry"]["coordinates"][:2]
                w = wind_at(lat, lon, props["datetime_utc"], client)
                if w:
                    props.update(
                        {
                            "wind_from_deg": w["wind_from_deg"],
                            "plume_toward_deg": w["plume_toward_deg"],
                            "wind_speed_ms": w["wind_speed_ms"],
                        }
                    )
                    winded += 1

    plumes_path.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    return {"plumes": len(feats), "with_imagery": matched, "with_wind": winded}


def run(plumes_path: Path, assets_dir: Path, limit: int = DEFAULT_ASSET_LIMIT) -> dict[str, Any]:
    config.load()
    print(f"fetching plume imagery (newest {limit})…")
    got = fetch_assets(assets_dir, limit=limit)
    for k, v in got.items():
        print(f"  {k}: {v}")
    print("attaching imagery and wind to the plume layer…")
    stats = enrich(plumes_path, assets_dir)
    for k, v in stats.items():
        print(f"  {k}: {v}")
    return {**got, **stats}
