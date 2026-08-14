"""Small GeoJSON helpers. Deliberately dependency-free for v0 —
heavy geo deps (geopandas/rasterio) arrive with the TROPOMI stages."""

from __future__ import annotations

from typing import Any, Iterator


def iter_coords(geometry: dict[str, Any]) -> Iterator[tuple[float, float]]:
    """Yield every (lon, lat) vertex in any GeoJSON geometry."""
    if geometry is None:
        return
    if geometry.get("type") == "GeometryCollection":
        for g in geometry.get("geometries", []):
            yield from iter_coords(g)
        return

    def walk(node: Any) -> Iterator[tuple[float, float]]:
        if (
            isinstance(node, (list, tuple))
            and len(node) >= 2
            and isinstance(node[0], (int, float))
            and isinstance(node[1], (int, float))
        ):
            yield float(node[0]), float(node[1])
        elif isinstance(node, (list, tuple)):
            for child in node:
                yield from walk(child)

    yield from walk(geometry.get("coordinates", []))


def intersects_bbox(geometry: dict[str, Any], bbox: tuple[float, float, float, float]) -> bool:
    """True if any vertex falls inside bbox (sufficient for ROI cropping of
    small features; long transcontinental lines are not a case in this ROI)."""
    lon_min, lat_min, lon_max, lat_max = bbox
    return any(
        lon_min <= lon <= lon_max and lat_min <= lat <= lat_max
        for lon, lat in iter_coords(geometry)
    )


def max_lat(geometry: dict[str, Any]) -> float:
    return max((lat for _, lat in iter_coords(geometry)), default=-90.0)


def feature_collection(features: list[dict[str, Any]]) -> dict[str, Any]:
    return {"type": "FeatureCollection", "features": features}
