"""Walking routes and place search.

Routes come from OpenRouteService (foot-walking) when ORS_API_KEY is set, with the community OSRM foot
server as a fallback. Both are public services with rate limits, so results are cached in memory for a
short time. The cache holds only coordinates and lives in this process; nothing is written to disk or to
the database, and origin and destination are never stored with a user.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Awaitable, Callable, Optional

import httpx

from services.geo import LatLng
from services.risk_engine import RouteGeom

log = logging.getLogger("pathlens.routing")

ORS_URL = "https://api.openrouteservice.org/v2/directions/foot-walking/geojson"
MAX_ROUTES = 3
_CACHE_MAX = 256
_cache: dict[str, tuple[float, object]] = {}


class RoutingError(Exception):
    """A routing or geocoding problem with a message that is safe to show to the user."""

    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.message = message
        self.status = status


def clear_cache() -> None:
    _cache.clear()


def _ttl() -> float:
    try:
        return float(os.getenv("ROUTE_CACHE_SECONDS", "3600"))
    except ValueError:
        return 3600.0


def _cache_get(key: str):
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < _ttl():
        return hit[1]
    _cache.pop(key, None)
    return None


def _cache_put(key: str, value: object) -> None:
    if len(_cache) >= _CACHE_MAX:
        _cache.pop(next(iter(_cache)))
    _cache[key] = (time.monotonic(), value)


def _user_agent() -> str:
    return os.getenv("HTTP_USER_AGENT", "PathLens-prototype/0.1")


async def _with_client(client: Optional[httpx.AsyncClient], fn: Callable[[httpx.AsyncClient], Awaitable]):
    if client is not None:
        return await fn(client)
    async with httpx.AsyncClient(timeout=15.0, headers={"User-Agent": _user_agent()}) as c:
        return await fn(c)


async def _ors(c: httpx.AsyncClient, key: str, origin: LatLng, dest: LatLng) -> list[RouteGeom]:
    r = await c.post(
        ORS_URL,
        headers={"Authorization": key, "Content-Type": "application/json"},
        json={
            "coordinates": [[origin[1], origin[0]], [dest[1], dest[0]]],
            "alternative_routes": {"target_count": MAX_ROUTES, "share_factor": 0.6, "weight_factor": 1.6},
            "instructions": False,
        },
    )
    if r.status_code != 200:
        raise RoutingError(f"OpenRouteService returned {r.status_code}.")
    out = []
    for f in r.json().get("features", [])[:MAX_ROUTES]:
        s = f["properties"]["summary"]
        coords = [(lat, lng) for lng, lat in f["geometry"]["coordinates"]]
        out.append(RouteGeom(coords, float(s["distance"]), float(s["duration"]), "OpenRouteService, foot-walking"))
    return out


async def _foot_osrm(c: httpx.AsyncClient, origin: LatLng, dest: LatLng) -> list[RouteGeom]:
    base = os.getenv("FOOT_ROUTER_URL", "https://routing.openstreetmap.de/routed-foot").rstrip("/")
    # The profile word in this URL is ignored by this server; the foot profile comes from the host path.
    url = f"{base}/route/v1/driving/{origin[1]},{origin[0]};{dest[1]},{dest[0]}"
    r = await c.get(url, params={"overview": "full", "geometries": "geojson", "alternatives": "true"})
    if r.status_code != 200:
        raise RoutingError(f"The foot routing server returned {r.status_code}. Try again, or set ORS_API_KEY for a more reliable service.")
    data = r.json()
    if data.get("code") not in (None, "Ok") or not data.get("routes"):
        raise RoutingError("No walking route found between those points.", 422)
    return [
        RouteGeom([(lat, lng) for lng, lat in rt["geometry"]["coordinates"]], float(rt["distance"]), float(rt["duration"]), "OSRM foot server (community)")
        for rt in data["routes"][:MAX_ROUTES]
    ]


async def get_routes(origin: LatLng, dest: LatLng, *, client: Optional[httpx.AsyncClient] = None) -> list[RouteGeom]:
    """Up to three walking routes between two points."""
    key = f"r|{origin[0]:.4f},{origin[1]:.4f}|{dest[0]:.4f},{dest[1]:.4f}"
    cached = _cache_get(key)
    if cached is not None:
        return list(cached)  # type: ignore[arg-type]

    ors_key = os.getenv("ORS_API_KEY", "").strip()

    async def run(c: httpx.AsyncClient) -> list[RouteGeom]:
        if ors_key:
            try:
                return await _ors(c, ors_key, origin, dest)
            except (RoutingError, httpx.HTTPError, KeyError, ValueError) as err:
                log.warning("OpenRouteService failed (%s); falling back to the community foot server.", err)
        return await _foot_osrm(c, origin, dest)

    try:
        routes = await _with_client(client, run)
    except RoutingError:
        raise
    except (httpx.HTTPError, KeyError, ValueError) as err:
        raise RoutingError("The routing service could not be reached. Try again in a moment.") from err
    routes = [r for r in routes if len(r.coords) >= 2]
    if not routes:
        raise RoutingError("No walking route found between those points.", 422)
    _cache_put(key, routes)
    return list(routes)


async def geocode(query: str, *, client: Optional[httpx.AsyncClient] = None) -> LatLng:
    """Turn a place name into coordinates using Nominatim."""
    q = " ".join(query.split())
    key = f"g|{q.lower()}"
    cached = _cache_get(key)
    if cached is not None:
        return cached  # type: ignore[return-value]
    base = os.getenv("NOMINATIM_URL", "https://nominatim.openstreetmap.org").rstrip("/")

    async def run(c: httpx.AsyncClient):
        return await c.get(f"{base}/search", params={"format": "json", "limit": 1, "q": q})

    try:
        r = await _with_client(client, run)
    except httpx.HTTPError as err:
        raise RoutingError("Place search is unavailable right now. Send coordinates instead.") from err
    if r.status_code != 200:
        raise RoutingError("Place search is unavailable right now. Send coordinates instead.")
    data = r.json()
    if not data:
        raise RoutingError(f"Could not find “{q}”. Try a fuller address or send coordinates.", 422)
    point = (float(data[0]["lat"]), float(data[0]["lon"]))
    _cache_put(key, point)
    return point
