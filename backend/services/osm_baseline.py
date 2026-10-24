"""OpenStreetMap baseline: streetlights, help points and surrounding activity.

This is the real, uniformly-structured data layer that exists no matter how many people have filed
reports. It feeds the informational layers in the route analysis (it never enters the score).

Take a snapshot ahead of time and store it, rather than querying Overpass while someone waits:
import it with POST /api/admin/baseline/import, or load a saved Overpass JSON file with
POST /api/admin/baseline/load.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Iterable, Optional, Sequence

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from models.infra import InfraFeature

HELP_AMENITIES = {"police", "hospital", "clinic", "pharmacy", "fire_station"}
ACTIVITY_AMENITIES = {
    "restaurant", "cafe", "fast_food", "bank", "atm", "marketplace", "place_of_worship", "school",
    "college", "university", "bus_station", "cinema", "library", "post_office", "pub", "bar",
}
MAX_BBOX_DEG = 0.1  # about 11 km per side; keeps Overpass requests small and polite


class BaselineError(Exception):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.message = message
        self.status = status


def validate_bbox(south: float, west: float, north: float, east: float) -> None:
    if not (-90 <= south < north <= 90 and -180 <= west < east <= 180):
        raise BaselineError("The bounding box is not valid. Use south < north and west < east.")
    if north - south > MAX_BBOX_DEG or east - west > MAX_BBOX_DEG:
        raise BaselineError(f"The bounding box is too large. Keep each side under {MAX_BBOX_DEG} degrees (about 11 km).")


def build_overpass_query(south: float, west: float, north: float, east: float) -> str:
    validate_bbox(south, west, north, east)
    box = f"({south:.6f},{west:.6f},{north:.6f},{east:.6f})"
    help_re = "|".join(sorted(HELP_AMENITIES))
    act_re = "|".join(sorted(ACTIVITY_AMENITIES))
    return (
        "[out:json][timeout:60];\n(\n"
        f'  node["highway"="street_lamp"]{box};\n'
        f'  nwr["amenity"~"^({help_re})$"]{box};\n'
        f'  nwr["amenity"~"^({act_re})$"]{box};\n'
        f'  nwr["shop"]{box};\n'
        ");\nout center tags;"
    )


def classify(tags: dict) -> Optional[tuple[str, str]]:
    """(kind, subtype) for an OSM element, or None if it is not a feature we use."""
    if tags.get("highway") == "street_lamp":
        return "lamp", "street_lamp"
    amenity = tags.get("amenity")
    if amenity in HELP_AMENITIES:
        return "help", amenity
    if amenity in ACTIVITY_AMENITIES:
        return "activity", amenity
    shop = tags.get("shop")
    if shop and shop not in ("vacant", "no"):
        return "activity", f"shop:{shop}"
    return None


def parse_overpass(data: dict) -> list[dict]:
    """Overpass JSON -> flat feature dicts. Ways and relations use their centre point."""
    out: list[dict] = []
    seen: set[str] = set()
    for el in data.get("elements", []):
        tags = el.get("tags") or {}
        kind = classify(tags)
        if kind is None:
            continue
        if "lat" in el and "lon" in el:
            lat, lng = el["lat"], el["lon"]
        elif "center" in el:
            lat, lng = el["center"]["lat"], el["center"]["lon"]
        else:
            continue
        ref = f"{el.get('type', 'node')}/{el.get('id')}"
        if ref in seen:
            continue
        seen.add(ref)
        out.append({"osm_ref": ref, "kind": kind[0], "subtype": kind[1][:40], "lat": float(lat), "lng": float(lng)})
    return out


async def fetch_overpass(south: float, west: float, north: float, east: float, *, client: Optional[httpx.AsyncClient] = None) -> dict:
    query = build_overpass_query(south, west, north, east)
    url = os.getenv("OVERPASS_URL", "https://overpass-api.de/api/interpreter")
    headers = {"User-Agent": os.getenv("HTTP_USER_AGENT", "PathLens-prototype/0.1")}

    async def run(c: httpx.AsyncClient) -> httpx.Response:
        return await c.post(url, data={"data": query}, headers=headers)

    try:
        if client is not None:
            r = await run(client)
        else:
            async with httpx.AsyncClient(timeout=90.0) as c:
                r = await run(c)
    except httpx.HTTPError as err:
        raise BaselineError("Overpass could not be reached. Load a saved snapshot with /api/admin/baseline/load instead.", 502) from err
    if r.status_code != 200:
        raise BaselineError(f"Overpass returned {r.status_code}. Try again later, or load a saved snapshot instead.", 502)
    return r.json()


def store_features(db: Session, features: Iterable[dict]) -> dict:
    """Insert new features and update existing ones (matched by osm_ref). Returns counts."""
    feats = list(features)
    now = datetime.now(timezone.utc)
    existing: dict[str, InfraFeature] = {}
    refs = [f["osm_ref"] for f in feats]
    for i in range(0, len(refs), 500):  # stay under SQLite's variable limit
        batch = refs[i : i + 500]
        for row in db.scalars(select(InfraFeature).where(InfraFeature.osm_ref.in_(batch))):
            existing[row.osm_ref] = row
    added = updated = 0
    for f in feats:
        row = existing.get(f["osm_ref"])
        if row is None:
            db.add(InfraFeature(osm_ref=f["osm_ref"], kind=f["kind"], subtype=f["subtype"], lat=f["lat"], lng=f["lng"], imported_at=now))
            added += 1
        else:
            row.kind, row.subtype, row.lat, row.lng, row.imported_at = f["kind"], f["subtype"], f["lat"], f["lng"], now
            updated += 1
    db.commit()
    return {"added": added, "updated": updated, "total_in_snapshot": len(feats)}
