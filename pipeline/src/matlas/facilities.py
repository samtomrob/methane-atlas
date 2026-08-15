"""Facility records — the thing no provider publishes.

Every methane provider publishes *plumes*: here is a detection, at this place,
on this day. Ask a different question — "what is the story of this mine?" — and
none of them can answer it, because a plume catalogue is organised by event, not
by asset.

This module inverts that. Each mapped facility gets a record combining:

  * its detection history, fused across all four providers
  * how many times a satellite has actually looked at it
  * a resulting status

The status is the point. A facility with no detections is ambiguous in every
existing catalogue — silence could mean clean, or could mean nobody pointed an
instrument at it. Separating those two is genuinely new information, and it is
what turns a map of plumes into a map of accountability:

  emitting            at least one confirmed detection
  watched, no plume   imaged repeatedly, nothing found — meaningfully clean
  under-observed      too few looks to conclude anything
  blind spot          never imaged at all

"Never detected" is the most-cited and least-examined statistic in methane
monitoring. Most of it turns out to be the last two categories.
"""

from __future__ import annotations

import collections
import datetime as dt
import json
import math
from pathlib import Path
from typing import Any

import httpx

CMR = "https://cmr.earthdata.nasa.gov/search/granules.json"
EMIT_ENH = "C3242680113-LPCLOUD"  # EMITL2BCH4ENH v002
UA = {"User-Agent": "methane-atlas/0.1 (open public-good project)"}

# Each plume is attributed to ONE facility — the nearest, as already assigned in
# the plume layer — rather than to everything within a radius. In the Hunter
# Valley the mines sit within a few km of each other, so a radius double-counts
# badly: Hunter Valley Operations claimed 91 detections that way against 24 on
# nearest-facility attribution, with neighbouring mines all claiming the same
# plumes. Nearby-but-not-nearest plumes are kept separately as context.
NEARBY_RADIUS_KM = 10.0

# Overpasses needed before "nothing found" carries any weight. From the PNG
# work, roughly a third of overpasses are usable after cloud, so ~10 raw
# overpasses is about 3-4 clear looks — the floor for saying anything.
WATCHED_THRESHOLD = 10

POINT_LAYERS = ("coal_mines", "gas_plants")


def _km(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    dx = (lon2 - lon1) * 111.32 * math.cos(math.radians((lat1 + lat2) / 2))
    dy = (lat2 - lat1) * 110.57
    return math.hypot(dx, dy)


def _load_point_facilities(data_dir: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for layer in POINT_LAYERS:
        path = data_dir / f"{layer}.geojson"
        if not path.exists():
            continue
        for f in json.loads(path.read_text(encoding="utf-8")).get("features", []):
            geom = f.get("geometry") or {}
            if geom.get("type") != "Point":
                continue
            lon, lat = geom["coordinates"][:2]
            p = f.get("properties", {})
            out.append(
                {
                    "id": p.get("id"),
                    "name": p.get("name") or "(unnamed)",
                    "layer": layer,
                    "subtype": p.get("subtype"),
                    "state": p.get("state"),
                    "country": p.get("country"),
                    "status_infra": p.get("status"),
                    "capacity_mw": p.get("capacity_mw"),
                    "lon": round(float(lon), 5),
                    "lat": round(float(lat), 5),
                }
            )
    return out


def emit_overpasses(lon: float, lat: float, client: httpx.Client) -> int | None:
    """How many times EMIT has imaged this spot, from CMR metadata alone.

    Counting granules rather than reading rasters keeps this to one cheap query
    per facility. It measures opportunity, not clear-sky success — the PNG scan
    showed roughly a third of overpasses survive cloud — but opportunity is
    exactly what distinguishes "clean" from "never looked at".
    """
    d = 0.03  # ~3 km box; EMIT granules are far larger, this just anchors the point
    try:
        r = client.get(
            CMR,
            params={
                "collection_concept_id": EMIT_ENH,
                "bounding_box": f"{lon-d},{lat-d},{lon+d},{lat+d}",
                "page_size": 1,
            },
            timeout=90,
        )
    except httpx.HTTPError:
        return None
    if r.status_code != 200:
        return None
    hits = r.headers.get("CMR-Hits")
    return int(hits) if hits and hits.isdigit() else None


def build(data_dir: Path, with_observability: bool = True) -> dict[str, Any]:
    plumes = json.loads((data_dir / "plumes.geojson").read_text(encoding="utf-8"))["features"]
    facilities = _load_point_facilities(data_dir)
    print(f"{len(facilities)} point facilities, {len(plumes)} plumes")

    # Group plumes by the facility they were already attributed to, so each
    # detection belongs to exactly one asset.
    #
    # Keyed on (name, layer), not name alone: several sites host both a coal
    # mine and a waste-coal-mine-gas power station under the same name — Appin
    # and Tahmoor both do — and keying on name credited the power station with
    # the mine's detections. In an accountability tool that is a misattribution,
    # not a rounding error.
    attributed: dict[tuple[str, str], list[dict[str, Any]]] = collections.defaultdict(list)
    for pl in plumes:
        name = pl["properties"].get("facility_name")
        layer = pl["properties"].get("facility_layer")
        if name and layer:
            attributed[(name, layer)].append(pl["properties"])

    records: list[dict[str, Any]] = []
    for fac in facilities:
        own = attributed.get((fac["name"], fac["layer"]), [])
        hits = [(0.0, p) for p in own]

        # Plumes near enough to matter but attributed elsewhere — useful
        # context in a dense basin, never counted as this facility's own.
        nearby = 0
        for pl in plumes:
            plon, plat = pl["geometry"]["coordinates"][:2]
            if abs(plon - fac["lon"]) > 0.15 or abs(plat - fac["lat"]) > 0.15:
                continue
            if (
                pl["properties"].get("facility_name") == fac["name"]
                and pl["properties"].get("facility_layer") == fac["layer"]
            ):
                continue
            if _km(fac["lon"], fac["lat"], plon, plat) <= NEARBY_RADIUS_KM:
                nearby += 1

        rec = dict(fac)
        rec["detections"] = len(hits)
        rec["nearby_other_facility"] = nearby
        if hits:
            dates = sorted((h[1].get("datetime_utc") or "")[:10] for h in hits if h[1].get("datetime_utc"))
            rates = [h[1]["emission_kg_hr"] for h in hits if h[1].get("emission_kg_hr")]
            provs = collections.Counter(h[1].get("provider") for h in hits)
            rec.update(
                {
                    "first_detection": dates[0] if dates else None,
                    "last_detection": dates[-1] if dates else None,
                    "providers": sorted(provs),
                    "provider_count": len(provs),
                    "max_rate_kg_hr": round(max(rates)) if rates else None,
                    "median_rate_kg_hr": round(sorted(rates)[len(rates) // 2]) if rates else None,
                    "closest_km": round(min((h[1].get("facility_km") or 0) for h in hits), 2),
                }
            )
        records.append(rec)

    if with_observability:
        print("querying EMIT coverage per facility…")
        with httpx.Client(headers=UA, follow_redirects=True) as client:
            for i, rec in enumerate(records, 1):
                rec["emit_overpasses"] = emit_overpasses(rec["lon"], rec["lat"], client)
                if i % 25 == 0:
                    print(f"  {i}/{len(records)}")

    # Classify.
    for rec in records:
        n = rec.get("emit_overpasses")
        if rec["detections"] > 0:
            rec["watch_status"] = "emitting"
        elif n is None:
            rec["watch_status"] = "unknown"
        elif n == 0:
            rec["watch_status"] = "blind spot"
        elif n >= WATCHED_THRESHOLD:
            rec["watch_status"] = "watched, no plume"
        else:
            rec["watch_status"] = "under-observed"

    records.sort(key=lambda r: (-r["detections"], -(r.get("emit_overpasses") or 0)))
    tally = collections.Counter(r["watch_status"] for r in records)

    summary = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "facilities": len(records),
        "nearby_radius_km": NEARBY_RADIUS_KM,
        "watched_threshold_overpasses": WATCHED_THRESHOLD,
        "by_status": dict(tally),
        "method": (
            "Each plume is attributed to its single nearest facility, so counts do "
            "not double-count across neighbouring mines. Overpasses count EMIT "
            "methane granules whose footprint contains the facility — opportunity "
            "to observe, not cloud-free success; roughly a third survive cloud. "
            "Proximity is not attribution: it indicates what is nearby, not what "
            "emitted."
        ),
    }
    return {"summary": summary, "facilities": records}


def _write_back(data_dir: Path, records: list[dict[str, Any]]) -> int:
    """Stamp watch status onto the infrastructure layers.

    The map styles facilities by status, and MapLibre cannot join to an external
    table, so the verdict has to live on the feature itself.
    """
    by_key = {(r["layer"], r["name"]): r for r in records}
    updated = 0
    for layer in POINT_LAYERS:
        path = data_dir / f"{layer}.geojson"
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        for f in data.get("features", []):
            rec = by_key.get((layer, (f.get("properties") or {}).get("name")))
            if not rec:
                continue
            f["properties"]["watch_status"] = rec["watch_status"]
            f["properties"]["detections"] = rec["detections"]
            if rec.get("emit_overpasses") is not None:
                f["properties"]["emit_overpasses"] = rec["emit_overpasses"]
            if rec.get("last_detection"):
                f["properties"]["last_detection"] = rec["last_detection"]
            if rec.get("max_rate_kg_hr") is not None:
                f["properties"]["max_rate_kg_hr"] = rec["max_rate_kg_hr"]
            updated += 1
        path.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    return updated


def run(data_dir: Path, out_path: Path, with_observability: bool = True) -> dict[str, Any]:
    result = build(data_dir, with_observability=with_observability)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, separators=(",", ":")), encoding="utf-8")
    stamped = _write_back(data_dir, result["facilities"])
    print(f"stamped watch status onto {stamped} map features")

    s = result["summary"]
    print(f"\n{s['facilities']} facility records")
    for status, n in sorted(s["by_status"].items(), key=lambda kv: -kv[1]):
        print(f"  {status:20s} {n:4d}")

    top = [r for r in result["facilities"] if r["detections"]][:10]
    if top:
        print("\nmost-detected:")
        for r in top:
            print(
                f"  {r['name'][:26]:26s} {r['detections']:3d} detections  "
                f"{r.get('first_detection')}..{r.get('last_detection')}  "
                f"peak {r.get('max_rate_kg_hr') or '—'} kg/hr  "
                f"{r.get('emit_overpasses')} EMIT passes"
            )

    blind = [r for r in result["facilities"] if r["watch_status"] in ("blind spot", "under-observed")]
    if blind:
        print(f"\n{len(blind)} facilities cannot be called clean — too few looks:")
        for r in blind[:8]:
            print(f"  {r['name'][:26]:26s} {r.get('emit_overpasses')} EMIT passes, 0 detections")
    print(f"\n-> {out_path}")
    return result["summary"]
