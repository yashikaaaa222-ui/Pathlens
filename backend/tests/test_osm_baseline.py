import asyncio

import httpx
import pytest

from models.infra import InfraFeature
from services import osm_baseline
from services.osm_baseline import BaselineError, build_overpass_query, parse_overpass, store_features

ELEMENTS = {
    "elements": [
        {"type": "node", "id": 1, "lat": 51.5, "lon": -0.09, "tags": {"highway": "street_lamp"}},
        {"type": "node", "id": 2, "lat": 51.5001, "lon": -0.0901, "tags": {"amenity": "pharmacy"}},
        {"type": "node", "id": 3, "lat": 51.5002, "lon": -0.0902, "tags": {"amenity": "cafe"}},
        {"type": "node", "id": 4, "lat": 51.5003, "lon": -0.0903, "tags": {"shop": "bakery"}},
        {"type": "node", "id": 5, "lat": 51.5004, "lon": -0.0904, "tags": {"shop": "vacant"}},  # not activity
        {"type": "way", "id": 6, "center": {"lat": 51.501, "lon": -0.091}, "tags": {"amenity": "hospital"}},
        {"type": "node", "id": 7, "lat": 51.5005, "lon": -0.0905, "tags": {"amenity": "bench"}},  # not used
        {"type": "node", "id": 1, "lat": 51.5, "lon": -0.09, "tags": {"highway": "street_lamp"}},  # repeated
        {"type": "way", "id": 8, "tags": {"amenity": "police"}},  # no position
    ]
}


def test_parse_sorts_features_into_kinds_and_skips_what_it_cannot_use():
    feats = {f["osm_ref"]: f for f in parse_overpass(ELEMENTS)}
    assert set(feats) == {"node/1", "node/2", "node/3", "node/4", "way/6"}
    assert feats["node/1"]["kind"] == "lamp"
    assert feats["node/2"]["kind"] == "help" and feats["way/6"]["kind"] == "help"
    assert feats["node/3"]["kind"] == "activity" and feats["node/4"]["subtype"] == "shop:bakery"
    assert feats["way/6"]["lat"] == 51.501  # ways use their centre point


def test_the_query_covers_all_three_kinds_inside_the_box():
    q = build_overpass_query(51.49, -0.11, 51.51, -0.08)
    assert "street_lamp" in q and "pharmacy" in q and '"shop"' in q and "out center" in q
    assert "(51.490000,-0.110000,51.510000,-0.080000)" in q


@pytest.mark.parametrize("box", [(51.5, -0.1, 51.4, -0.05), (0, 0, 1, 1), (95, 0, 96, 1)])
def test_bad_or_huge_boxes_are_refused(box):
    with pytest.raises(BaselineError):
        build_overpass_query(*box)


def test_storing_a_snapshot_twice_updates_instead_of_duplicating(db):
    feats = parse_overpass(ELEMENTS)
    first = store_features(db, feats)
    second = store_features(db, feats)
    assert first == {"added": 5, "updated": 0, "total_in_snapshot": 5}
    assert second["added"] == 0 and second["updated"] == 5
    assert db.query(InfraFeature).count() == 5


def test_fetch_posts_the_query_and_maps_failures_to_clear_errors():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.content.decode()
        return httpx.Response(200, json=ELEMENTS)

    async def ok():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await osm_baseline.fetch_overpass(51.49, -0.11, 51.51, -0.08, client=c)

    assert asyncio.run(ok()) == ELEMENTS and "street_lamp" in seen["body"]

    async def down():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(429))) as c:
            return await osm_baseline.fetch_overpass(51.49, -0.11, 51.51, -0.08, client=c)

    with pytest.raises(BaselineError) as err:
        asyncio.run(down())
    assert err.value.status == 502 and "snapshot" in err.value.message
