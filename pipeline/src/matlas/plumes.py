"""Point-source methane plumes from every public provider, normalised into one layer.

This is the layer that can actually say "something at this location is emitting at
roughly this rate" — which the TROPOMI composites explicitly cannot (see
docs/FINDINGS.md: surface bias there exceeds the real basin signal, and PNG has
essentially no coverage at all). Plume imagers resolve tens of metres and report a
flux per detection, so they are the project's only route to facility-scale
evidence and its only route to any PNG signal.

Providers degrade independently: one being unconfigured or down never sinks the
run, it just contributes nothing and says so.

Association to infrastructure is deliberately phrased as *nearest mapped
infrastructure*, never attribution. A plume 800 m from a coal mine is evidence
worth showing a human; it is not proof that the mine emitted it.
"""

from __future__ import annotations

import collections
import csv
import datetime as dt
import io
import json
import re
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

import httpx

from . import ROI, config
from .hotspots import _distance_km, _load_infrastructure

LON_MIN, LAT_MIN, LON_MAX, LAT_MAX = ROI
UA = {"User-Agent": "Mozilla/5.0 (compatible; methane-atlas/0.1; open public-good project)"}

CM_BASE = "https://api.carbonmapper.org/api/v1"
CMR_GRANULES = "https://cmr.earthdata.nasa.gov/search/granules.umm_json"
EMIT_COLLECTION = "C3242707413-LPCLOUD"

# How close a plume must be to mapped infrastructure before we say anything at
# all about what is nearby, and the bands used to qualify that statement.
ASSOCIATION_MAX_KM = 10.0
HIGH_CONFIDENCE_KM = 1.0
MEDIUM_CONFIDENCE_KM = 3.0


@dataclass
class Plume:
    plume_id: str
    provider: str
    instrument: str | None
    datetime_utc: str | None
    lon: float
    lat: float
    emission_kg_hr: float | None = None
    uncertainty_kg_hr: float | None = None
    sector: str | None = None
    scene_id: str | None = None
    provider_url: str | None = None
    quality: str | None = None
    offshore: bool | None = None
    nearest_name: str | None = None
    nearest_layer: str | None = None
    nearest_km: float | None = None
    association: str | None = None
    # Nearest point facility recorded separately from the nearest pipeline: a
    # long line is "nearest" to a great many plumes simply because it is long,
    # which makes it a weak association even at a short distance.
    facility_name: str | None = None
    facility_layer: str | None = None
    facility_km: float | None = None
    notes: list[str] = field(default_factory=list)


def _in_roi(lon: float, lat: float) -> bool:
    return LON_MIN <= lon <= LON_MAX and LAT_MIN <= lat <= LAT_MAX


def _num(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out else None  # reject NaN


def _centroid(geometry: dict[str, Any] | None) -> tuple[float, float] | None:
    if not geometry:
        return None
    pts: list[tuple[float, float]] = []

    def walk(node: Any) -> None:
        if (
            isinstance(node, (list, tuple))
            and len(node) >= 2
            and isinstance(node[0], (int, float))
            and isinstance(node[1], (int, float))
        ):
            pts.append((float(node[0]), float(node[1])))
        elif isinstance(node, (list, tuple)):
            for child in node:
                walk(child)

    walk(geometry.get("coordinates", []))
    if not pts:
        return None
    return sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)


# --------------------------------------------------------------------------
# Carbon Mapper — Tanager-1, EMIT and aircraft, with measured emission rates.
# Richest Australian source: ~271 plumes since 2023, ~51 in the Bowen Basin.
# --------------------------------------------------------------------------


def _carbon_mapper_token(client: httpx.Client) -> str | None:
    """Prefer the login over a stored token.

    Carbon Mapper access tokens are extremely short-lived — a freshly minted one
    was measured expiring the same day. Preferring a stored CARBON_MAPPER_TOKEN,
    as this once did, means an unattended job picks up a credential that dies
    within hours and then fails every morning. Where an email and password
    exist they are used first, because they mint a fresh token on every run;
    the stored token is only a fallback for setups that have nothing else.
    """
    email = config.get("CARBON_MAPPER_EMAIL")
    password = config.get("CARBON_MAPPER_PASSWORD")
    if email and password:
        r = client.post(
            f"{CM_BASE}/token/pair", json={"email": email, "password": password}, timeout=60
        )
        if r.status_code == 200:
            minted = r.json().get("access")
            if minted:
                return minted
        # Fall through to the stored token rather than failing outright — a
        # changed password should degrade, not take the whole run down.
        print(f"    (login failed with {r.status_code}; trying stored token)")
    return config.get("CARBON_MAPPER_TOKEN")


def fetch_carbon_mapper() -> tuple[list[Plume], str]:
    out: list[Plume] = []
    with httpx.Client(headers=UA, follow_redirects=True) as client:
        try:
            token = _carbon_mapper_token(client)
        except (httpx.HTTPError, RuntimeError) as e:
            return [], f"sign-in failed ({e})"
        if not token:
            return [], "not configured — see docs/ACCOUNTS_PHASE2.md"

        headers = {"Authorization": f"Bearer {token}"}
        offset, limit = 0, 500
        while True:
            r = client.get(
                f"{CM_BASE}/catalog/plumes/annotated",
                params={
                    # bbox must be four repeated numeric params; a comma string
                    # is rejected. sort takes fixed literals only — anything
                    # like "-published_at" returns 422.
                    "bbox": [LON_MIN, LAT_MIN, LON_MAX, LAT_MAX],
                    "plume_gas": "CH4",
                    "limit": limit,
                    "offset": offset,
                    "sort": "published_desc",
                },
                headers=headers,
                timeout=120,
            )
            if r.status_code in (401, 403):
                return out, f"token rejected ({r.status_code})"
            r.raise_for_status()
            payload = r.json()
            items = payload.get("items", payload if isinstance(payload, list) else [])
            # `nearby_items` are outside the bbox — deliberately ignored.
            for it in items:
                centre = _centroid(it.get("geometry_json"))
                if not centre or not _in_roi(*centre):
                    continue
                pid = it.get("plume_id") or it.get("id")
                out.append(
                    Plume(
                        plume_id=f"cm:{pid}",
                        provider="Carbon Mapper",
                        instrument=it.get("instrument") or it.get("platform"),
                        datetime_utc=it.get("scene_timestamp") or it.get("published_at"),
                        lon=round(centre[0], 5),
                        lat=round(centre[1], 5),
                        emission_kg_hr=_num(it.get("emission_auto")),
                        uncertainty_kg_hr=_num(it.get("emission_uncertainty_auto")),
                        sector=it.get("sector"),
                        scene_id=it.get("scene_id"),
                        provider_url=f"https://data.carbonmapper.org/plume/{pid}",
                        quality=str(it.get("plume_quality") or "") or None,
                        offshore=it.get("is_offshore"),
                    )
                )
            if len(items) < limit:
                break
            offset += len(items)
    return out, f"{len(out)} plumes"


# --------------------------------------------------------------------------
# NASA EMIT — 60 m plume complexes. CMR metadata search is anonymous, so
# locations and dates work without a token; emission rates live inside the
# granule files, which do need Earthdata credentials.
# --------------------------------------------------------------------------


def _emit_location(umm: dict[str, Any]) -> tuple[float | None, float | None]:
    """Centre of an EMIT granule's footprint.

    Plume complexes are delivered as GPolygons tracing the plume outline — not
    as Points or BoundingRectangles, which is what an earlier version assumed
    and why it silently found nothing over Australia.
    """
    geo = umm.get("SpatialExtent", {}).get("HorizontalSpatialDomain", {}).get("Geometry", {})

    for polygon in geo.get("GPolygons", []) or []:
        pts = polygon.get("Boundary", {}).get("Points", []) or []
        lons = [_num(p.get("Longitude")) for p in pts]
        lats = [_num(p.get("Latitude")) for p in pts]
        lons = [v for v in lons if v is not None]
        lats = [v for v in lats if v is not None]
        if lons and lats:
            return sum(lons) / len(lons), sum(lats) / len(lats)

    if geo.get("Points"):
        p = geo["Points"][0]
        return _num(p.get("Longitude")), _num(p.get("Latitude"))

    for b in geo.get("BoundingRectangles", []) or []:
        w, e = _num(b.get("WestBoundingCoordinate")), _num(b.get("EastBoundingCoordinate"))
        s, n = _num(b.get("SouthBoundingCoordinate")), _num(b.get("NorthBoundingCoordinate"))
        if None not in (w, e, s, n):
            return (w + e) / 2, (s + n) / 2

    return None, None


def fetch_emit() -> tuple[list[Plume], str]:
    out: list[Plume] = []
    token = config.get("EARTHDATA_TOKEN")
    headers = dict(UA)
    if token:
        headers["Authorization"] = f"Bearer {token}"

    with httpx.Client(headers=headers, follow_redirects=True) as client:
        page, seen = 1, 0
        while True:
            r = client.get(
                CMR_GRANULES,
                params={
                    "collection_concept_id": EMIT_COLLECTION,
                    "bounding_box": f"{LON_MIN},{LAT_MIN},{LON_MAX},{LAT_MAX}",
                    "page_size": 2000,
                    "page_num": page,
                },
                timeout=120,
            )
            if r.status_code in (401, 403):
                return out, f"CMR rejected the request ({r.status_code})"
            r.raise_for_status()
            items = r.json().get("items", [])
            for it in items:
                umm = it.get("umm", {})
                gid = umm.get("GranuleUR", "")
                lon, lat = _emit_location(umm)
                if lon is None or lat is None or not _in_roi(lon, lat):
                    continue
                temporal = umm.get("TemporalExtent", {})
                start = temporal.get("SingleDateTime") or temporal.get(
                    "RangeDateTime", {}
                ).get("BeginningDateTime")
                out.append(
                    Plume(
                        plume_id=f"emit:{gid}",
                        provider="NASA EMIT",
                        instrument="EMIT",
                        datetime_utc=start,
                        lon=round(lon, 5),
                        lat=round(lat, 5),
                        scene_id=gid,
                        provider_url="https://earth.jpl.nasa.gov/emit/data/data-portal/Greenhouse-Gases/",
                        notes=[] if token else ["rate needs EARTHDATA_TOKEN"],
                    )
                )
            seen += len(items)
            if len(items) < 2000:
                break
            page += 1

    note = f"{len(out)} plume complexes"
    if not token:
        note += " (locations only — set EARTHDATA_TOKEN for emission rates)"
    return out, note


# --------------------------------------------------------------------------
# SRON weekly TROPOMI super-emitter list. 7 km localisation, so flagged coarse.
# --------------------------------------------------------------------------

SRON_INDEX = "https://www.sron.nl/en/pillars/science/earth/methane/methane-plume-maps/"

# The weekly CSV URLs are not constructible: WordPress adds unpredictable -1/-2
# dedup suffixes, the /YYYY/MM/ upload directory tracks upload date rather than
# data week (2025 wk52 lives under /2026/01/), and from 2026 wk17 the filename
# went lowercase and dropped the directory entirely. So scrape the index.
SRON_CSV_RE = re.compile(
    r"https://www\.sron\.nl/wp-content/uploads/(?:\d{4}/\d{2}/)?"
    r"sron_weekly_methane_plumes_(\d{4})_wk(\d+)_v(\d{8})(?:-\d+)?\.csv",
    re.IGNORECASE,
)


def _sron_csv_urls(client: httpx.Client) -> list[str]:
    r = client.get(SRON_INDEX)
    r.raise_for_status()
    # Keep the highest dedup suffix per (year, week) — later uploads supersede.
    best: dict[tuple[str, str], str] = {}
    for match in SRON_CSV_RE.finditer(r.text):
        key = (match.group(1), match.group(2).zfill(2))
        url = match.group(0)
        if key not in best or url > best[key]:
            best[key] = url
    return [best[k] for k in sorted(best)]


def fetch_sron() -> tuple[list[Plume], str]:
    out: list[Plume] = []
    with httpx.Client(headers=UA, follow_redirects=True, timeout=90) as client:
        try:
            urls = _sron_csv_urls(client)
        except httpx.HTTPError as e:
            return [], f"index unreachable ({type(e).__name__})"
        if not urls:
            return [], "index reachable but no CSV links matched"

        weeks = 0
        for url in urls:
            try:
                r = client.get(url)
            except httpx.HTTPError:
                continue
            if r.status_code != 200:
                continue
            weeks += 1
            for row in csv.DictReader(io.StringIO(r.text)):
                low = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
                lon, lat = _num(low.get("lon")), _num(low.get("lat"))
                if lon is None or lat is None or not _in_roi(lon, lat):
                    continue
                date = low.get("date", "")  # YYYYMMDD
                iso = f"{date[:4]}-{date[4:6]}-{date[6:8]}" if len(date) == 8 else None
                time_utc = low.get("time_utc")
                if iso and time_utc:
                    iso = f"{iso}T{time_utc}Z"
                # SRON reports tonnes per hour; everything else here is kg/hr.
                rate_t = _num(low.get("source_rate_t/h"))
                unc_t = _num(low.get("uncertainty_t/h"))
                out.append(
                    Plume(
                        plume_id=f"sron:{date}-{lat:.3f}-{lon:.3f}",
                        provider="SRON",
                        instrument="TROPOMI",
                        datetime_utc=iso,
                        lon=round(lon, 5),
                        lat=round(lat, 5),
                        emission_kg_hr=rate_t * 1000 if rate_t is not None else None,
                        uncertainty_kg_hr=unc_t * 1000 if unc_t is not None else None,
                        provider_url=SRON_INDEX,
                        notes=["TROPOMI 7 km footprint — position approximate"],
                    )
                )
    return out, f"{len(out)} detections from {weeks} weekly files"


# --------------------------------------------------------------------------
# UNEP IMEO / MARS — validated multi-instrument detections, coal and waste
# sectors added in 2026. No account needed.
# --------------------------------------------------------------------------

# The portal itself blocks scripted access, but the data it serves lives in an
# open Azure blob that needs no special headers. Whole-file download only —
# query parameters are ignored, so filtering happens locally.
IMEO_PLUMES_ZIP = (
    "https://unepazeconomyadlsstorage.blob.core.windows.net/public/"
    "unep_methanedata_detected_plumes_csv.zip"
)
IMEO_MEMBER = "unep_methanedata_detected_plumes.csv"


def fetch_imeo() -> tuple[list[Plume], str]:
    out: list[Plume] = []
    try:
        r = httpx.get(IMEO_PLUMES_ZIP, headers=UA, timeout=300, follow_redirects=True)
        r.raise_for_status()
    except httpx.HTTPError as e:
        return [], f"blob unreachable ({type(e).__name__})"

    try:
        archive = zipfile.ZipFile(io.BytesIO(r.content))
        member = next(n for n in archive.namelist() if n.endswith(IMEO_MEMBER))
        text = archive.read(member).decode("utf-8", errors="replace")
    except (zipfile.BadZipFile, StopIteration, KeyError) as e:
        return [], f"unexpected archive layout ({type(e).__name__})"

    total = 0
    for row in csv.DictReader(io.StringIO(text)):
        total += 1
        lon, lat = _num(row.get("lon")), _num(row.get("lat"))
        if lon is None or lat is None or not _in_roi(lon, lat):
            continue
        out.append(
            Plume(
                plume_id=f"imeo:{row.get('id_plume')}",
                provider="UNEP IMEO",
                instrument=row.get("satellite"),
                datetime_utc=row.get("tile_date"),
                lon=round(lon, 5),
                lat=round(lat, 5),
                emission_kg_hr=_num(row.get("ch4_fluxrate")),
                uncertainty_kg_hr=_num(row.get("ch4_fluxrate_std")),
                sector=row.get("sector"),
                scene_id=row.get("tile"),
                provider_url="https://methanedata.unep.org/plumes",
                notes=(
                    [f"source {row['source_name']}"] if row.get("source_name") else []
                ),
            )
        )
    return out, f"{len(out)} in region (of {total:,} worldwide)"


PROVIDERS: dict[str, Callable[[], tuple[list[Plume], str]]] = {
    "Carbon Mapper": fetch_carbon_mapper,
    "NASA EMIT": fetch_emit,
    "UNEP IMEO": fetch_imeo,
    "SRON": fetch_sron,
}


POINT_LAYERS = ("coal_mines", "gas_plants")


def associate(plumes: list[Plume], infra: list[dict[str, Any]]) -> None:
    """Tag each plume with nearby mapped infrastructure and a confidence band.

    Point facilities and linear infrastructure are tracked separately. A
    transmission pipeline runs for hundreds of kilometres, so it ends up
    "nearest" to a large share of plumes purely by extent — naming it as the
    single association overstates a weak link. Where a point facility is within
    range it becomes the headline association instead.

    Proximity only. This says what is nearby, never what emitted.
    """
    for p in plumes:
        best_point: tuple[float, dict[str, Any]] | None = None
        best_any: tuple[float, dict[str, Any]] | None = None
        for feat in infra:
            is_point = feat["layer"] in POINT_LAYERS
            for lon, lat in feat["coords"]:
                # Cheap reject before the trig.
                if abs(lon - p.lon) > 0.2 or abs(lat - p.lat) > 0.2:
                    continue
                d = _distance_km(p.lon, p.lat, lon, lat)
                if best_any is None or d < best_any[0]:
                    best_any = (d, feat)
                if is_point and (best_point is None or d < best_point[0]):
                    best_point = (d, feat)

        if best_point is not None and best_point[0] <= ASSOCIATION_MAX_KM:
            p.facility_name = best_point[1]["name"]
            p.facility_layer = best_point[1]["layer"]
            p.facility_km = round(best_point[0], 2)

        # Prefer a point facility as the headline when it is within range.
        chosen = best_point if (best_point and best_point[0] <= ASSOCIATION_MAX_KM) else best_any
        if chosen is None or chosen[0] > ASSOCIATION_MAX_KM:
            p.association = "none within %.0f km" % ASSOCIATION_MAX_KM
            continue
        dist, feat = chosen
        p.nearest_name = feat["name"]
        p.nearest_layer = feat["layer"]
        p.nearest_km = round(dist, 2)
        linear_only = feat["layer"] not in POINT_LAYERS
        p.association = (
            "linear-only" if linear_only
            else "high" if dist <= HIGH_CONFIDENCE_KM
            else "medium" if dist <= MEDIUM_CONFIDENCE_KM
            else "low"
        )


def dedupe(plumes: list[Plume]) -> list[Plume]:
    """Collapse the same event reported by more than one provider: within 1 km
    and the same day. Carbon Mapper wins where available since it carries a
    measured rate."""
    priority = {"Carbon Mapper": 0, "NASA EMIT": 1, "UNEP IMEO": 2, "SRON": 3}
    ordered = sorted(plumes, key=lambda p: (priority.get(p.provider, 9), p.plume_id))
    kept: list[Plume] = []
    for p in ordered:
        day = (p.datetime_utc or "")[:10]
        duplicate = None
        for k in kept:
            if (k.datetime_utc or "")[:10] != day:
                continue
            if abs(k.lon - p.lon) > 0.02 or abs(k.lat - p.lat) > 0.02:
                continue
            if _distance_km(p.lon, p.lat, k.lon, k.lat) <= 1.0:
                duplicate = k
                break
        if duplicate is None:
            kept.append(p)
        else:
            duplicate.notes.append(f"also reported by {p.provider}")
    return kept


def run(out_path: Path, data_dir: Path) -> dict[str, Any]:
    config.load()
    collected: list[Plume] = []
    status: dict[str, str] = {}

    for name, fetch in PROVIDERS.items():
        try:
            found, note = fetch()
        except Exception as e:  # a provider outage must not sink the run
            found, note = [], f"error: {type(e).__name__}: {str(e)[:100]}"
        status[name] = note
        collected.extend(found)
        print(f"  {name:16s} {note}")

    # Refuse to publish a worse dataset than last time.
    #
    # The first unattended run shipped without Carbon Mapper credentials. It
    # published 500 plumes over the top of 798, stripped the imagery and wind
    # enrichment, and reported 37 "new" detections that were really UNEP
    # records previously deduplicated against the missing provider's copies.
    # A refresh that loses a provider is a failure, not an update.
    state_path = out_path.parent / "plumes_seen.json"
    prior_counts: dict[str, int] = {}
    if state_path.exists():
        try:
            prior_counts = json.loads(state_path.read_text()).get("provider_counts", {})
        except (ValueError, OSError):
            prior_counts = {}

    now_counts = collections.Counter(p.provider for p in collected)
    regressions = [
        f"{name}: {prior} -> {now_counts.get(name, 0)}"
        for name, prior in prior_counts.items()
        if prior > 0 and now_counts.get(name, 0) == 0
    ]
    if regressions:
        raise SystemExit(
            "Refusing to publish: a provider that previously returned data now returns "
            "none, which would overwrite the live layer with a smaller one.\n  "
            + "\n  ".join(regressions)
            + "\nCheck credentials, or pass --allow-regression if the loss is intended."
        )

    before = len(collected)
    plumes = dedupe(collected)
    infra = _load_infrastructure(data_dir)
    associate(plumes, infra)

    near = sum(1 for p in plumes if p.nearest_km is not None)
    at_facility = sum(1 for p in plumes if p.facility_km is not None)
    high = sum(1 for p in plumes if p.association == "high")
    print(
        f"\n{len(plumes)} plumes after dedupe (from {before})\n"
        f"  {at_facility} within {ASSOCIATION_MAX_KM:.0f} km of a point facility "
        f"(mine or gas plant), {high} within {HIGH_CONFIDENCE_KM:.0f} km\n"
        f"  {near} near any mapped infrastructure including pipelines"
    )

    features = []
    for p in plumes:
        props = {k: v for k, v in asdict(p).items() if k not in ("lon", "lat") and v not in (None, [], "")}
        # MapLibre GeoJSON sources carry strings and numbers only; a list here
        # is invisible to the map, so join it rather than shipping dead weight.
        if isinstance(props.get("notes"), list):
            props["notes"] = "; ".join(str(n) for n in props["notes"])
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [p.lon, p.lat]},
                "properties": props,
            }
        )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps({"type": "FeatureCollection", "features": features}, separators=(",", ":")),
        encoding="utf-8",
    )

    # Freshness, and what is new since the last run. Both are surfaced in the
    # UI and drive the alert feed: providers publish on a ~30-day delay, so the
    # useful signal is not "is this live" but "what arrived since I last looked".
    previous_ids: set[str] = set()
    state_path = out_path.parent / "plumes_seen.json"
    if state_path.exists():
        try:
            previous_ids = set(json.loads(state_path.read_text()).get("ids", []))
        except (ValueError, OSError):
            previous_ids = set()
    current_ids = {p.plume_id for p in plumes}
    new_ids = current_ids - previous_ids if previous_ids else set()

    def _age_days(p: Plume) -> int | None:
        raw = (p.datetime_utc or "")[:10]
        try:
            return (dt.date.today() - dt.date.fromisoformat(raw)).days
        except ValueError:
            return None

    ages = [(a, p) for p in plumes if (a := _age_days(p)) is not None]
    newest_age, newest_plume = min(ages, key=lambda t: t[0]) if ages else (None, None)

    new_plumes = sorted(
        (p for p in plumes if p.plume_id in new_ids),
        key=lambda p: p.emission_kg_hr or 0,
        reverse=True,
    )

    summary = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "count": len(plumes),
        "newest_detection": newest_plume.datetime_utc if newest_plume else None,
        "newest_days_old": newest_age,
        "new_since_last_run": len(new_plumes),
        "first_run": not previous_ids,
        "by_provider": status,
        "near_infrastructure": near,
        "near_facility": at_facility,
        "high_confidence": high,
        "association_note": (
            "Distance to the nearest mapped infrastructure. Proximity is not "
            "attribution — it indicates what is nearby, not what emitted."
        ),
        "rate_note": (
            "Each rate is an instantaneous snapshot at one overpass, in kg/hr. "
            "These must never be summed across plumes or extrapolated to annual "
            "totals: detections span several years, sample different moments, and "
            "are biased toward large, detectable events."
        ),
    }
    (out_path.parent / "plumes_status.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    # The alert feed: what appeared this run, richest first. Consumed by the
    # scheduled job to decide whether anything is worth notifying about.
    feed = {
        "generated_at": summary["generated_at"],
        "first_run": summary["first_run"],
        "new_count": len(new_plumes),
        "new_plumes": [
            {
                "plume_id": p.plume_id,
                "provider": p.provider,
                "datetime_utc": p.datetime_utc,
                "lon": p.lon,
                "lat": p.lat,
                "emission_kg_hr": p.emission_kg_hr,
                "sector": p.sector,
                "nearest_facility": p.facility_name,
                "facility_km": p.facility_km,
                "provider_url": p.provider_url,
            }
            for p in new_plumes[:100]
        ],
    }
    (out_path.parent / "plumes_new.json").write_text(json.dumps(feed, indent=2), encoding="utf-8")
    state_path.write_text(
        json.dumps(
            {
                "updated": summary["generated_at"],
                "provider_counts": dict(now_counts),
                "ids": sorted(current_ids),
            }
        ),
        encoding="utf-8",
    )

    if newest_age is not None:
        print(f"newest detection {newest_age} days old ({newest_plume.datetime_utc[:10]})")
    if previous_ids:
        print(f"new since last run: {len(new_plumes)}")
    else:
        print("first run — baseline recorded, no alerts emitted")
    print(f"-> {out_path}")
    return summary
