"""Infrastructure layer compile, v0: the no-credential sources.

Sources here need no account or token. Auth-gated enrichment (GEM tracker
spreadsheets for status/owner/capacity, OGIM wells, CER reported-emissions
joins) lands in later phases; features already carry the canonical schema so
those joins slot in without reshaping existing outputs.
"""

from __future__ import annotations

import io
import json
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from . import ROI
from .geo import feature_collection, intersects_bbox, iter_coords

UA = {
    "User-Agent": "methane-atlas-pipeline/0.1 (open public-good project; contact via repo)"
}

GA_PIPES = "https://services.ga.gov.au/gis/rest/services/Oil_Gas_Pipelines/MapServer/{layer}/query"
GA_MINES = "https://services.ga.gov.au/gis/rest/services/AustralianOperatingMines/MapServer/{layer}/query"
GEM_TARBALL = "https://api.github.com/repos/GlobalEnergyMonitor/GOIT-GGIT-pipeline-routes/tarball"
OPEN_ELEC = "https://data.openelectricity.org.au/v3/geo/au_facilities.json"

# No Australian gas/oil infrastructure sits north of 10°S. But the ROI's
# north-west corner also contains Indonesia and Timor-Leste, so PNG needs both
# bounds: north of 10°S AND east of 140.8°E (the 141°E border, with margin for
# the Gulf of Papua subsea leg which stays east of 144°E).
PNG_LAT_SPLIT = -10.0
PNG_LON_MIN = 140.8

GA_ATTRIB = "© Commonwealth of Australia (Geoscience Australia), CC BY 4.0"
GEM_ATTRIB = "Global Energy Monitor pipeline routes, CC BY 4.0"
OE_ATTRIB = "Open Electricity (OpenNEM) facility registry, CC BY-NC 4.0"


def _normalise_geometry(geometry: dict[str, Any] | None) -> dict[str, Any] | None:
    """Ensure a geometry carries a `type`.

    Open Electricity returns geometries with `coordinates` but no `type`, which
    is invalid GeoJSON: MapLibre silently refuses to draw them, so all 70 gas
    power stations were absent from the map while still appearing in the counts.
    Infer the type from the coordinate nesting depth.
    """
    if not geometry or "coordinates" not in geometry:
        return geometry
    if geometry.get("type"):
        return geometry
    node: Any = geometry["coordinates"]
    depth = 0
    while isinstance(node, (list, tuple)) and node and isinstance(node[0], (list, tuple)):
        depth += 1
        node = node[0]
    inferred = {0: "Point", 1: "LineString", 2: "Polygon", 3: "MultiPolygon"}.get(depth)
    if not inferred:
        return geometry
    return {**geometry, "type": inferred}


def _feature(
    geometry: dict[str, Any],
    *,
    fid: str,
    layer: str,
    name: str,
    subtype: str,
    status: str,
    country: str,
    state: str | None,
    source: str,
    source_url: str | None,
    license_: str,
    **extra: Any,
) -> dict[str, Any]:
    props: dict[str, Any] = {
        "id": fid,
        "layer": layer,
        "name": name or "(unnamed)",
        "subtype": subtype,
        "status": (status or "unknown").strip().lower(),
        "country": country,
        "state": state,
        "source": source,
        "source_url": source_url,
        "license": license_,
    }
    props.update({k: v for k, v in extra.items() if v not in (None, "", " ")})
    return {"type": "Feature", "geometry": _normalise_geometry(geometry), "properties": props}


def _arcgis_features(client: httpx.Client, url: str) -> list[dict[str, Any]]:
    """Paginate an ArcGIS REST layer query as GeoJSON."""
    out: list[dict[str, Any]] = []
    offset = 0
    while True:
        r = client.get(
            url,
            params={
                "where": "1=1",
                "outFields": "*",
                "f": "geojson",
                "resultOffset": offset,
            },
        )
        r.raise_for_status()
        feats = r.json().get("features", [])
        out.extend(feats)
        if len(feats) < 2000:
            return out
        offset += len(feats)


def fetch_ga_pipelines(client: httpx.Client) -> tuple[list[dict], list[dict]]:
    result: list[list[dict]] = []
    for layer, subtype in ((1, "gas"), (0, "oil")):
        feats = []
        for f in _arcgis_features(client, GA_PIPES.format(layer=layer)):
            p = f.get("properties", {})
            if not f.get("geometry"):
                continue
            feats.append(
                _feature(
                    f["geometry"],
                    fid=f"ga:pipe{layer}:{p.get('objectid')}",
                    layer=f"pipelines_{subtype}",
                    name=p.get("name"),
                    subtype=p.get("feature_type") or f"{subtype} pipeline",
                    status=p.get("operational_status"),
                    country="AU",
                    state=p.get("state"),
                    source="Geoscience Australia",
                    source_url="https://digital.atlas.gov.au/datasets/digitalatlas::gas-pipelines/about",
                    license_="CC BY 4.0",
                    length_km=p.get("length"),
                    spatial_confidence=p.get("spatial_confidence"),
                )
            )
        result.append(feats)
    return result[0], result[1]


def fetch_ga_coal_mines(client: httpx.Client) -> list[dict]:
    feats = []
    for layer, status in ((0, "operating"), (1, "developing"), (2, "care and maintenance")):
        for f in _arcgis_features(client, GA_MINES.format(layer=layer)):
            p = f.get("properties", {})
            if not f.get("geometry"):
                continue
            if (p.get("commodity_group") or "").strip().lower() != "coal":
                continue
            feats.append(
                _feature(
                    f["geometry"],
                    fid=f"ga:mine{layer}:{p.get('objectid')}",
                    layer="coal_mines",
                    name=p.get("name"),
                    subtype="coal mine",
                    status=p.get("status") or status,
                    country="AU",
                    state=p.get("state"),
                    source="Geoscience Australia (Australian Operating Mines)",
                    source_url="https://services.ga.gov.au/gis/rest/services/AustralianOperatingMines/MapServer",
                    license_="CC BY 4.0",
                )
            )
    return feats


def fetch_gem_png_routes(client: httpx.Client) -> tuple[list[dict], list[dict]]:
    """Download GEM's pipeline-routes repo tarball once; keep routes touching
    the ROI north of PNG_LAT_SPLIT. Australian domestic lines stay with the
    authoritative Geoscience Australia layer to avoid double-drawing; GEM's
    AU coverage (e.g. proposed lines) gets merged once the GGIT spreadsheet
    (owner form) provides status/owner attributes to dedupe on."""
    gas: list[dict] = []
    liquid: list[dict] = []
    with tempfile.TemporaryFile() as tmp:
        with client.stream("GET", GEM_TARBALL, timeout=600) as r:
            r.raise_for_status()
            for chunk in r.iter_bytes():
                tmp.write(chunk)
        tmp.seek(0)
        with tarfile.open(fileobj=tmp, mode="r:gz") as tar:
            for member in tar:
                path = member.name
                if not path.endswith(".geojson"):
                    continue
                if "/data/individual-routes/gas-pipelines/" in path:
                    bucket, subtype = gas, "gas"
                elif "/data/individual-routes/liquid-pipelines/" in path:
                    bucket, subtype = liquid, "liquid"
                else:
                    continue
                fh = tar.extractfile(member)
                if fh is None:
                    continue
                try:
                    fc = json.load(io.TextIOWrapper(fh, encoding="utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                project_id = Path(path).stem.split("-")[0]
                for f in fc.get("features", []):
                    geom = f.get("geometry")
                    if not geom or not intersects_bbox(geom, ROI):
                        continue
                    if not any(
                        lat > PNG_LAT_SPLIT and lon > PNG_LON_MIN
                        for lon, lat in iter_coords(geom)
                    ):
                        continue
                    p = f.get("properties", {})
                    bucket.append(
                        _feature(
                            geom,
                            fid=f"gem:{project_id}:{p.get('OBJECTID', len(bucket))}",
                            layer=f"pipelines_{'gas' if subtype == 'gas' else 'oil'}",
                            # Region files carry only ProjectID; real names land
                            # with the GGIT xlsx join (owner download form).
                            name=p.get("name") or f"GEM pipeline {project_id}",
                            subtype=f"{p.get('Product') or subtype} pipeline".lower(),
                            status="unknown",
                            country="PG",
                            state=p.get("State"),
                            source="Global Energy Monitor",
                            source_url=p.get("SrceLink"),
                            license_="CC BY 4.0",
                            gem_project_id=project_id,
                            length_km=p.get("Length"),
                        )
                    )
    return gas, liquid


def fetch_gas_plants(client: httpx.Client) -> list[dict]:
    r = client.get(OPEN_ELEC)
    r.raise_for_status()
    feats = []
    for f in r.json().get("features", []):
        p = f.get("properties", {})
        if not f.get("geometry"):
            continue
        units = p.get("duid_data") or []
        gas_units = [u for u in units if str(u.get("fuel_tech", "")).startswith("gas")]
        if not gas_units:
            continue
        fuel_techs = sorted({u["fuel_tech"] for u in gas_units})
        capacity = sum(u.get("capacity_registered") or 0 for u in gas_units)
        statuses = {str(u.get("status", "")).lower() for u in gas_units}
        status = "operating" if "operating" in statuses else (sorted(statuses)[0] if statuses else "unknown")
        feats.append(
            _feature(
                f["geometry"],
                fid=f"oe:{p.get('facility_id') or p.get('station_code')}",
                layer="gas_plants",
                name=p.get("name"),
                subtype="waste coal mine gas" if fuel_techs == ["gas_wcmg"] else "gas power station",
                status=status,
                country="AU",
                state=p.get("state"),
                source="Open Electricity",
                source_url="https://openelectricity.org.au/",
                license_="CC BY-NC 4.0",
                fuel_techs=", ".join(fuel_techs),
                capacity_mw=round(capacity, 1) if capacity else None,
                network=p.get("network"),
            )
        )
    return feats


def run(out_dir: Path) -> dict[str, int]:
    out_dir.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    with httpx.Client(headers=UA, timeout=120, follow_redirects=True) as client:
        ga_gas, ga_oil = fetch_ga_pipelines(client)
        gem_gas, gem_liquid = fetch_gem_png_routes(client)
        layers = {
            "pipelines_gas": ga_gas + gem_gas,
            "pipelines_oil": ga_oil + gem_liquid,
            "coal_mines": fetch_ga_coal_mines(client),
            "gas_plants": fetch_gas_plants(client),
        }
    for name, feats in layers.items():
        (out_dir / f"{name}.geojson").write_text(
            json.dumps(feature_collection(feats), separators=(",", ":")),
            encoding="utf-8",
        )
        counts[name] = len(feats)
    status = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "roi": ROI,
        "layers": counts,
        "attribution": [GA_ATTRIB, GEM_ATTRIB, OE_ATTRIB],
        "pending_sources": [
            "TROPOMI composites (CDSE/GEE tokens)",
            "EMIT + Carbon Mapper + IMEO + SRON plumes (phase 2)",
            "GEM tracker spreadsheets (owner download form)",
            "OGIM wells/processing (bulk ingest)",
            "CER reported-emissions join (phase 3)",
        ],
    }
    (out_dir / "status.json").write_text(json.dumps(status, indent=2), encoding="utf-8")
    return counts
