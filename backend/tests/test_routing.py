import asyncio

import httpx
import pytest

from services import routing
from services.routing import RoutingError

O, D = (51.5, -0.1), (51.5, -0.085)

ORS_BODY = {
    "features": [
        {"geometry": {"coordinates": [[-0.1, 51.5], [-0.0925, 51.5], [-0.085, 51.5]]}, "properties": {"summary": {"distance": 1040.5, "duration": 750.2}}},
        {"geometry": {"coordinates": [[-0.1, 51.5], [-0.0925, 51.502], [-0.085, 51.5]]}, "properties": {"summary": {"distance": 1120.0, "duration": 820.0}}},
    ]
}
OSRM_BODY = {
    "code": "Ok",
    "routes": [{"geometry": {"coordinates": [[-0.1, 51.5], [-0.085, 51.5]]}, "distance": 1030.0, "duration": 740.0}],
}


def run(handler, call):
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await call(c)

    return asyncio.run(go())


def test_openrouteservice_is_used_first_when_a_key_is_set(monkeypatch):
    monkeypatch.setenv("ORS_API_KEY", "key123")
    seen = {}

    def handler(request):
        seen["url"], seen["auth"] = str(request.url), request.headers.get("authorization")
        return httpx.Response(200, json=ORS_BODY)

    routes = run(handler, lambda c: routing.get_routes(O, D, client=c))
    assert "foot-walking" in seen["url"] and seen["auth"] == "key123"
    assert len(routes) == 2 and routes[0].coords[0] == (51.5, -0.1)  # (lat, lng) order
    assert routes[0].distance == 1040.5 and "OpenRouteService" in routes[0].source


def test_a_failing_key_falls_back_to_the_foot_server(monkeypatch):
    monkeypatch.setenv("ORS_API_KEY", "bad")

    def handler(request):
        if "openrouteservice" in str(request.url):
            return httpx.Response(403)
        return httpx.Response(200, json=OSRM_BODY)

    routes = run(handler, lambda c: routing.get_routes(O, D, client=c))
    assert len(routes) == 1 and "OSRM foot" in routes[0].source


def test_foot_server_url_uses_lng_lat_order_and_asks_for_alternatives():
    seen = {}

    def handler(request):
        seen["path"], seen["q"] = request.url.path, dict(request.url.params)
        return httpx.Response(200, json=OSRM_BODY)

    run(handler, lambda c: routing.get_routes(O, D, client=c))
    assert seen["path"].endswith("/-0.1,51.5;-0.085,51.5")
    assert seen["q"]["alternatives"] == "true" and seen["q"]["geometries"] == "geojson"


def test_results_are_cached_so_the_public_servers_are_not_hammered():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(200, json=OSRM_BODY)

    run(handler, lambda c: routing.get_routes(O, D, client=c))
    run(handler, lambda c: routing.get_routes(O, D, client=c))
    assert len(calls) == 1


@pytest.mark.parametrize(
    "response,status",
    [(httpx.Response(500), 502), (httpx.Response(200, json={"code": "NoRoute", "routes": []}), 422)],
)
def test_server_problems_become_clear_errors(response, status):
    with pytest.raises(RoutingError) as err:
        run(lambda r: response, lambda c: routing.get_routes(O, D, client=c))
    assert err.value.status == status


def test_network_failure_becomes_a_clear_error():
    def handler(request):
        raise httpx.ConnectError("boom")

    with pytest.raises(RoutingError) as err:
        run(handler, lambda c: routing.get_routes(O, D, client=c))
    assert "could not be reached" in err.value.message


def test_geocode_returns_a_point_and_caches_it():
    calls = []

    def handler(request):
        calls.append(dict(request.url.params))
        return httpx.Response(200, json=[{"lat": "51.5074", "lon": "-0.1278"}])

    assert run(handler, lambda c: routing.geocode("  Trafalgar   Square ", client=c)) == (51.5074, -0.1278)
    run(handler, lambda c: routing.geocode("trafalgar square", client=c))
    assert len(calls) == 1 and calls[0]["q"] == "Trafalgar Square"


def test_geocode_reports_places_it_cannot_find():
    with pytest.raises(RoutingError) as err:
        run(lambda r: httpx.Response(200, json=[]), lambda c: routing.geocode("Nowhereville", client=c))
    assert err.value.status == 422 and "Nowhereville" in err.value.message
