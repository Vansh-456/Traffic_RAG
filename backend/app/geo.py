"""Small geometry helpers (WGS84, metres) shared across modules."""

from __future__ import annotations

import math
from collections.abc import Sequence

from shapely.geometry import LineString, Point, mapping
from shapely.geometry.base import BaseGeometry

# Delhi NCR bounding box (lon_min, lat_min, lon_max, lat_max)
DELHI_BBOX = (76.84, 28.40, 77.35, 28.88)
EARTH_R = 6_371_000.0


def in_delhi(lat: float, lon: float) -> bool:
    return DELHI_BBOX[1] <= lat <= DELHI_BBOX[3] and DELHI_BBOX[0] <= lon <= DELHI_BBOX[2]


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_R * math.asin(math.sqrt(a))


def meters_to_degrees(meters: float, lat: float = 28.6) -> float:
    """Approximate degrees for a metric buffer at Delhi's latitude (avg of lat/lon scale)."""
    lat_deg = meters / 111_320.0
    lon_deg = meters / (111_320.0 * math.cos(math.radians(lat)))
    return (lat_deg + lon_deg) / 2


def line_from_latlon(points: Sequence[Sequence[float]]) -> LineString:
    return LineString([(p[1], p[0]) for p in points])


def point(lat: float, lon: float) -> Point:
    return Point(lon, lat)


def buffer_m(geom: BaseGeometry, meters: float) -> BaseGeometry:
    return geom.buffer(meters_to_degrees(meters, geom.centroid.y))


def distance_m(a: BaseGeometry, b: BaseGeometry) -> float:
    """Approx. minimum distance in metres between two geometries."""
    if a.intersects(b):
        return 0.0
    from shapely.ops import nearest_points

    pa, pb = nearest_points(a, b)
    return haversine_m(pa.y, pa.x, pb.y, pb.x)


def to_geojson(geom: BaseGeometry) -> dict[str, object]:
    return dict(mapping(geom))
