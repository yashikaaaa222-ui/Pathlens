"""The real PathLens API, with only the two outside lookups (walking routes and place search) replaced by fixed
answers. Use it when you cannot reach OpenStreetMap services (a sandbox, CI, an offline demo).

    python tests/e2e/stub_backend.py 8000        # then use the places "Alpha" and "Beta"

Everything else is the real application: database, scoring, moderation, sign-in, photos.
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "backend"))
os.chdir(os.path.join(ROOT, "backend"))

from services import routing  # noqa: E402
from services.risk_engine import RouteGeom  # noqa: E402


# The two stub routes and the two places come from the synthetic corridor in data/roads, so the demo data and the
# browser tests share one source.
with open(os.path.join(ROOT, "data", "roads", "demo_corridor.geojson")) as f:
    _features = json.load(f)["features"]
_lines = {x["properties"]["name"]: x for x in _features if x["geometry"]["type"] == "LineString"}


def _coords(name):
    return [(lat, lng) for lng, lat in _lines[name]["geometry"]["coordinates"]]


ROUTE_A, ROUTE_B = _coords("Route A"), _coords("Route B")
PLACES = {x["properties"]["name"].lower(): (x["geometry"]["coordinates"][1], x["geometry"]["coordinates"][0]) for x in _features if x["geometry"]["type"] == "Point"}
_DIST = {n: (x["properties"]["distance_m"], x["properties"]["duration_s"]) for n, x in _lines.items()}


async def fake_routes(origin, dest, *, client=None):
    return [RouteGeom(list(ROUTE_A), *_DIST["Route A"], "stub"), RouteGeom(list(ROUTE_B), *_DIST["Route B"], "stub")]


async def fake_geocode(query, *, client=None):
    try:
        return PLACES[query.strip().lower()]
    except KeyError:
        raise routing.RoutingError("In stub mode only the places Alpha and Beta are known.", 404) from None


routing.get_routes = fake_routes
routing.geocode = fake_geocode

if __name__ == "__main__":
    import uvicorn
    from main import app

    uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[1]) if len(sys.argv) > 1 else 8000, log_level="warning")
