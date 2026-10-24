"""Simulated demo reports.

Every row made here is flagged `simulated=True` with `source="simulated"`, is hidden from the public
report list and from route comparisons unless the caller asks for it, and can be wiped with one call.
Reports cluster in part of each route on purpose, so coverage gaps show up in a demo.

Ids are deterministic (derived from the route), so seeding the same route twice updates rows instead
of piling up duplicates.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta
from typing import Sequence

from services.geo import chunk_route
from services.risk_engine import CATEGORIES, RouteGeom

DEMO_NOTE = "Simulated demo report"


def simulate_reports(route: RouteGeom, route_index: int, now: datetime) -> list[dict]:
    chunks = chunk_route(route.coords, 100.0)
    if not chunks:
        return []
    first = route.coords[0]
    seed = (int(first[0] * 1e5) ^ int(first[1] * 1e5) ^ (route_index * 7919) ^ (len(chunks) * 131)) & 0xFFFFFFFF
    rng = random.Random(seed)
    start = rng.random() * 0.4
    end = min(1.0, start + 0.4 + rng.random() * 0.4)
    cats = list(CATEGORIES)
    out: list[dict] = []
    for k in range(3 + rng.randrange(8)):
        pos = start + (end - start) * rng.random()
        coords, _ = chunks[min(len(chunks) - 1, int(pos * len(chunks)))]
        lat, lng = coords[rng.randrange(len(coords))]
        roll = rng.random()
        status = "verified" if roll < 0.45 else "unverified" if roll < 0.85 else "resolved" if roll < 0.93 else "rejected"
        age = 1 + rng.randrange(150)
        reported_at = now - timedelta(days=age)
        out.append(
            {
                "id": f"sim-{seed:08x}-{k}",
                "category": rng.choice(cats),
                "lat": lat + (rng.random() - 0.5) * 0.00012,  # about 7 m either way
                "lng": lng + (rng.random() - 0.5) * 0.00012,
                "description": DEMO_NOTE,
                "status": status,
                "source": "simulated",
                "simulated": True,
                "reporter_id": f"sim-{seed:08x}-{k}",  # every simulated report is its own reporter
                "reported_at": reported_at,
                "verified_at": now - timedelta(days=age // 2) if status == "verified" else None,
            }
        )
    return out


def simulate_for_routes(routes: Sequence[RouteGeom], now: datetime) -> list[dict]:
    out: list[dict] = []
    for i, r in enumerate(routes):
        out.extend(simulate_reports(r, i, now))
    return out
