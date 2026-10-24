"""Rebuilds the SYNTHETIC sample files in data/roads and data/synthetic.

    python data/build_synthetic.py            # rewrite the files
    python data/build_synthetic.py --check    # exit 1 if a committed file differs from what this script makes

Nothing here is real. The corridor is two invented walking routes between the placeholder places "Alpha" and
"Beta"; the OpenStreetMap-style snapshot uses negative element ids (the OSM convention for objects that do not
exist) and says so in its own header; the simulated reports come from the same generator as the "Simulated demo
data" button (backend/services/demo_data.py), with a fixed date so the file is reproducible.

Everything is deterministic, so a test can compare the committed files with a fresh build.
"""
from __future__ import annotations

import csv
import io
import json
import os
import random
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "backend"))

from ai.seed_reports import SEED  # noqa: E402
from services.demo_data import simulate_reports  # noqa: E402
from services.geo import haversine  # noqa: E402
from services.risk_engine import RouteGeom  # noqa: E402

NOTE = "SYNTHETIC SAMPLE. Invented for demos and tests. Not real roads, not real OpenStreetMap data, not real reports."
FIXED_NOW = datetime(2026, 10, 1, 0, 0, tzinfo=timezone.utc)  # the "today" of the simulated reports file

PATHS = {
    "corridor": os.path.join(HERE, "roads", "demo_corridor.geojson"),
    "snapshot": os.path.join(HERE, "roads", "osm_snapshot_synthetic.json"),
    "seed": os.path.join(HERE, "synthetic", "classifier_seed.csv"),
    "simulated": os.path.join(HERE, "synthetic", "simulated_reports.csv"),
}


def line(lat: float, lng0: float, lng1: float, n: int = 40) -> list[tuple[float, float]]:
    return [(round(lat, 6), round(lng0 + (lng1 - lng0) * i / n, 6)) for i in range(n + 1)]


ALPHA = (51.5, -0.1)
BETA = (51.5, -0.085)
ROUTE_A = line(51.5000, -0.1000, -0.0850)
ROUTE_B = [ALPHA] + line(51.5020, -0.0985, -0.0865) + [BETA]
WALKING_SPEED_MS = 1.4  # about 5 km/h


def _route(name: str, coords: list[tuple[float, float]]) -> tuple[str, list[tuple[float, float]], float, float]:
    """Distance and time come from the geometry itself, so the numbers on a route card always match its line."""
    metres = round(sum(haversine(a, b) for a, b in zip(coords, coords[1:])))
    return name, coords, float(metres), float(round(metres / WALKING_SPEED_MS))


ROUTES = [_route("Route A", ROUTE_A), _route("Route B", ROUTE_B)]  # name, coordinates (lat, lng), metres, seconds


def corridor() -> dict:
    feats = []
    for name, coords, dist, secs in ROUTES:
        feats.append(
            {
                "type": "Feature",
                "properties": {"name": name, "distance_m": dist, "duration_s": secs, "synthetic": True},
                "geometry": {"type": "LineString", "coordinates": [[lng, lat] for lat, lng in coords]},
            }
        )
    for name, (lat, lng) in (("Alpha", ALPHA), ("Beta", BETA)):
        feats.append({"type": "Feature", "properties": {"name": name, "synthetic": True}, "geometry": {"type": "Point", "coordinates": [lng, lat]}})
    return {"type": "FeatureCollection", "note": NOTE, "features": feats}


def snapshot() -> dict:
    """Overpass-style JSON, ready for POST /api/admin/baseline/load. Negative ids mark every element as not real."""
    rng = random.Random(11)
    elements: list[dict] = []
    next_id = [-1]

    def node(lat: float, lng: float, tags: dict) -> None:
        elements.append({"type": "node", "id": next_id[0], "lat": round(lat, 6), "lon": round(lng, 6), "tags": {**tags, "note": "synthetic sample"}})
        next_id[0] -= 1

    # Streetlights along the first 60% of Route A, one every ~110 m, then nothing: a deliberate unlit stretch.
    a = ROUTE_A
    for i in range(0, int(len(a) * 0.6), 4):
        lat, lng = a[i]
        node(lat + 0.00006, lng + rng.uniform(-0.00004, 0.00004), {"highway": "street_lamp"})
    # Fewer lamps on Route B, so the two routes differ in the informational layer.
    for i in range(3, len(ROUTE_B) - 3, 9):
        lat, lng = ROUTE_B[i]
        node(lat + 0.00006, lng + rng.uniform(-0.00004, 0.00004), {"highway": "street_lamp"})
    # Help points and activity near the start of the corridor.
    node(51.50030, -0.09700, {"amenity": "pharmacy", "name": "Synthetic Pharmacy (sample)"})
    node(51.50045, -0.09300, {"amenity": "clinic", "name": "Synthetic Clinic (sample)"})
    node(51.50028, -0.08800, {"amenity": "police", "name": "Synthetic Police Post (sample)"})
    node(51.49960, -0.09900, {"amenity": "hospital", "name": "Synthetic Hospital (sample)"})
    node(51.49965, -0.08600, {"amenity": "fire_station", "name": "Synthetic Fire Station (sample)"})
    for k, (amenity, name) in enumerate([("cafe", "Cafe"), ("restaurant", "Restaurant"), ("cafe", "Tea Stall"), ("bank", "Bank Branch"), ("school", "School")]):
        node(51.50032 + rng.uniform(-0.00003, 0.00003), -0.0980 + 0.0022 * k, {"amenity": amenity, "name": f"Synthetic {name} (sample)"})
    node(51.50038, -0.0905, {"shop": "convenience", "name": "Synthetic Corner Shop (sample)"})
    return {
        "version": 0.6,
        "generator": "PathLens synthetic sample (NOT OpenStreetMap data)",
        "osm3s": {"copyright": "None. " + NOTE},
        "elements": elements,
    }


def _csv(fieldnames: list[str], rows: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fieldnames, lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()


def seed_csv() -> str:
    return _csv(["text", "category"], [{"text": t, "category": c} for t, c in SEED])


def simulated_csv() -> str:
    cols = ["id", "category", "lat", "lng", "description", "status", "source", "simulated", "reported_at", "verified_at"]
    rows = []
    for idx, (_, coords, dist, secs) in enumerate(ROUTES):
        for r in simulate_reports(RouteGeom(list(coords), dist, secs, "synthetic"), idx, FIXED_NOW):
            rows.append(
                {
                    "id": r["id"], "category": r["category"], "lat": round(r["lat"], 6), "lng": round(r["lng"], 6),
                    "description": r["description"], "status": r["status"], "source": r["source"], "simulated": "true",
                    "reported_at": r["reported_at"].isoformat(), "verified_at": r["verified_at"].isoformat() if r["verified_at"] else "",
                }
            )
    return _csv(cols, rows)


def build() -> dict[str, str]:
    return {
        PATHS["corridor"]: json.dumps(corridor(), indent=1) + "\n",
        PATHS["snapshot"]: json.dumps(snapshot(), indent=1) + "\n",
        PATHS["seed"]: seed_csv(),
        PATHS["simulated"]: simulated_csv(),
    }


def main() -> int:
    files = build()
    if "--check" in sys.argv:
        stale = [p for p, text in files.items() if not os.path.exists(p) or open(p, newline="").read() != text]
        for p in stale:
            print("differs:", os.path.relpath(p, ROOT))
        return 1 if stale else 0
    for p, text in files.items():
        with open(p, "w", newline="") as f:
            f.write(text)
        print("wrote", os.path.relpath(p, ROOT), f"({len(text):,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
