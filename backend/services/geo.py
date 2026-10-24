"""Geometry helpers. Coordinates are (lat, lng) tuples in degrees everywhere in this backend."""
from __future__ import annotations

import math
from typing import Sequence

LatLng = tuple[float, float]

EARTH_RADIUS_M = 6_371_000.0
M_PER_DEG_LAT = 110_540.0


def _rad(deg: float) -> float:
    return deg * math.pi / 180.0


def m_per_deg_lng(lat: float) -> float:
    return 111_320.0 * math.cos(_rad(lat))


def haversine(a: LatLng, b: LatLng) -> float:
    """Great-circle distance in metres."""
    d_lat = _rad(b[0] - a[0])
    d_lng = _rad(b[1] - a[1])
    h = math.sin(d_lat / 2) ** 2 + math.cos(_rad(a[0])) * math.cos(_rad(b[0])) * math.sin(d_lng / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(h)))


def point_to_segment(p: LatLng, a: LatLng, b: LatLng) -> float:
    """Metres from p to segment a-b, using a local flat projection (accurate at street scale)."""
    kx = m_per_deg_lng(p[0])
    ky = M_PER_DEG_LAT
    ax, ay = (a[1] - p[1]) * kx, (a[0] - p[0]) * ky
    bx, by = (b[1] - p[1]) * kx, (b[0] - p[0]) * ky
    dx, dy = bx - ax, by - ay
    l2 = dx * dx + dy * dy
    t = 0.0 if l2 == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / l2))
    return math.hypot(ax + t * dx, ay + t * dy)


def dist_to_polyline(p: LatLng, coords: Sequence[LatLng]) -> float:
    if not coords:
        return math.inf
    if len(coords) == 1:
        return haversine(p, coords[0])
    best = math.inf
    for i in range(1, len(coords)):
        best = min(best, point_to_segment(p, coords[i - 1], coords[i]))
    return best


def chunk_route(coords: Sequence[LatLng], size: float = 100.0) -> list[tuple[list[LatLng], float]]:
    """Split a polyline into consecutive chunks of about `size` metres.

    Returns (coords, length_m) pairs. The last chunk may be shorter; a tail under 1 m is dropped.
    """
    if len(coords) < 2 or size <= 0:
        return []
    chunks: list[tuple[list[LatLng], float]] = []
    cur: list[LatLng] = [coords[0]]
    acc = 0.0
    for i in range(1, len(coords)):
        a = coords[i - 1]
        b = coords[i]
        seg = haversine(a, b)
        while seg > 0 and acc + seg >= size:
            t = (size - acc) / seg
            m = (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
            cur.append(m)
            chunks.append((cur, size))
            cur = [m]
            a = m
            seg = haversine(a, b)
            acc = 0.0
        cur.append(b)
        acc += seg
    if acc > 1 and len(cur) > 1:
        chunks.append((cur, acc))
    return chunks


def bbox(coords: Sequence[LatLng], pad_m: float = 0.0) -> tuple[float, float, float, float]:
    """(south, west, north, east) of the points, padded by pad_m metres on every side."""
    lats = [c[0] for c in coords]
    lngs = [c[1] for c in coords]
    south, north = min(lats), max(lats)
    west, east = min(lngs), max(lngs)
    mid_lat = (south + north) / 2
    d_lat = pad_m / M_PER_DEG_LAT
    d_lng = pad_m / max(1.0, m_per_deg_lng(mid_lat))
    return south - d_lat, west - d_lng, north + d_lat, east + d_lng
