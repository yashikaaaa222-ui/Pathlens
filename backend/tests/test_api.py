from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

import database
from models.infra import InfraFeature
from models.report import Report, ReportConfirmation, ReportEvent
from services import routing
from services.routing import RoutingError

from conftest import ADMIN, COMPARE_BODY, MID_A

REPORT = {"category": "streetlight", "lat": MID_A[0], "lng": MID_A[1], "description": "Light is out near the corner", "consent": True}


def ago(**kw):
    return datetime.now(timezone.utc) - timedelta(**kw)


def counts(db):
    return {m.__name__: db.scalar(select(func.count()).select_from(m)) for m in (Report, ReportEvent, ReportConfirmation, InfraFeature)}


# ---- report intake -----------------------------------------------------------------------------


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["status"] == "ok" and r.json()["classifier_ready"] is True


def test_a_new_report_is_saved_as_unverified_with_an_audit_event(client, db):
    r = client.post("/api/reports", json=REPORT)
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "unverified" and body["category_source"] == "user" and body["similar_nearby"] == 0
    assert "only counts" in body["message"]
    assert db.get(Report, body["id"]).reporter_id.startswith("anon:")
    events = client.get(f"/api/reports/{body['id']}/events", headers=ADMIN).json()
    assert [(e["action"], e["actor"]) for e in events] == [("created", "public")]


def test_category_is_suggested_from_clear_text_and_refused_for_unclear_text(client):
    ok = client.post("/api/reports", json={"lat": 51.5, "lng": -0.09, "description": "Tree has fallen across the road", "consent": True})
    assert ok.status_code == 201 and ok.json()["category"] == "obstruction" and ok.json()["category_source"] == "classifier"
    bad = client.post("/api/reports", json={"lat": 51.5, "lng": -0.09, "description": "asdf qwerty", "consent": True})
    assert bad.status_code == 422 and "Choose a category" in bad.json()["detail"]["message"]
    assert bad.json()["detail"]["suggestion"]["needs_review"] is True


@pytest.mark.parametrize(
    "patch,fragment",
    [
        ({"description": "call me on +91 98765 43210"}, "phone numbers"),
        ({"description": "mail me at someone@example.com"}, "email"),
        ({"lat": 123}, "lat"),
        ({"surprise": 1}, "surprise"),
        ({"description": "x" * 281}, "description"),
    ],
)
def test_bad_reports_are_rejected(client, patch, fragment):
    r = client.post("/api/reports", json={**REPORT, **patch})
    assert r.status_code == 422 and fragment in r.text


def test_a_nearby_report_of_the_same_kind_is_flagged_as_a_possible_duplicate(client, db):
    first = client.post("/api/reports", json=REPORT).json()
    second = client.post("/api/reports", json={**REPORT, "lat": MID_A[0] + 0.0001}).json()
    assert second["similar_nearby"] == 1
    assert db.get(Report, second["id"]).possible_duplicate_of == first["id"]
    assert db.get(Report, first["id"]).possible_duplicate_of is None


def test_reporting_is_rate_limited_per_client(client):
    codes = [client.post("/api/reports", json=REPORT).status_code for _ in range(11)]
    assert codes[:10] == [201] * 10 and codes[10] == 429


def test_the_classify_endpoint_suggests_without_storing(client, db):
    r = client.post("/api/reports/classify", json={"text": "street light is not working"})
    assert r.status_code == 200 and r.json()["category"] == "streetlight" and "synthetic" in r.json()["note"]
    assert counts(db)["Report"] == 0


# ---- public listing ----------------------------------------------------------------------------


def test_public_list_hides_rejected_simulated_and_unverified_text(client, make_report):
    make_report(status="verified", description="Verified text")
    make_report(status="unverified", description="Unmoderated text", lat=51.5001)
    make_report(status="rejected", lat=51.5002)
    make_report(status="verified", simulated=True, source="simulated", lat=51.5003)
    rows = client.get("/api/reports").json()
    assert {r["status"] for r in rows} == {"verified", "unverified"} and len(rows) == 2
    by_status = {r["status"]: r for r in rows}
    assert by_status["verified"]["description"] == "Verified text"
    assert by_status["unverified"]["description"] is None
    assert not any(k in rows[0] for k in ("reporter_id", "photo_url", "classifier_category"))
    assert len(client.get("/api/reports", params={"include_simulated": True}).json()) == 3


def test_public_list_filters_by_box_and_requires_all_four_sides(client, make_report):
    make_report(lat=51.5, lng=-0.09)
    make_report(lat=52.5, lng=-0.09)
    box = {"min_lat": 51.4, "max_lat": 51.6, "min_lng": -0.2, "max_lng": 0.0}
    assert len(client.get("/api/reports", params=box).json()) == 1
    assert client.get("/api/reports", params={"min_lat": 51.4}).status_code == 422


# ---- moderation --------------------------------------------------------------------------------


def test_admin_endpoints_are_off_without_a_token_and_closed_to_wrong_ones(client, make_report, monkeypatch):
    r = make_report()
    assert client.patch(f"/api/reports/{r.id}/status", json={"status": "verified"}).status_code == 401
    assert client.patch(f"/api/reports/{r.id}/status", json={"status": "verified"}, headers={"X-Admin-Token": "nope"}).status_code == 401
    monkeypatch.delenv("ADMIN_TOKEN")
    off = client.patch(f"/api/reports/{r.id}/status", json={"status": "verified"}, headers=ADMIN)
    assert off.status_code == 503 and "switched off" in off.json()["detail"]


def test_moderation_follows_allowed_transitions_and_keeps_an_audit_trail(client, make_report, db):
    r = make_report()
    bad = client.patch(f"/api/reports/{r.id}/status", json={"status": "resolved"}, headers=ADMIN)
    assert bad.status_code == 409 and bad.json()["detail"]["allowed"] == ["rejected", "verified"]

    ok = client.patch(f"/api/reports/{r.id}/status", json={"status": "verified", "note": "Checked the photo"}, headers=ADMIN)
    assert ok.status_code == 200 and ok.json()["status"] == "verified" and ok.json()["verified_at"]

    assert client.patch(f"/api/reports/{r.id}/status", json={"status": "resolved"}, headers=ADMIN).status_code == 200
    reopened = client.patch(f"/api/reports/{r.id}/status", json={"status": "unverified"}, headers=ADMIN).json()
    assert reopened["status"] == "unverified" and reopened["verified_at"] is None  # no stale verification time

    trail = client.get(f"/api/reports/{r.id}/events", headers=ADMIN).json()
    assert [e["action"] for e in trail] == ["created", "status:verified", "status:resolved", "status:unverified"]
    assert trail[1]["actor"] == "admin" and trail[1]["note"] == "Checked the photo"
    assert client.patch("/api/reports/missing/status", json={"status": "verified"}, headers=ADMIN).status_code == 404


def test_a_field_audit_report_is_verified_and_labelled_as_such(client):
    audit = {k: v for k, v in REPORT.items() if k != "consent"}  # a field audit is made by the team, so it needs no consent
    r = client.post("/api/reports/field-audit", json={**audit, "auditor": "asha"}, headers=ADMIN)
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "verified" and body["source"] == "field_audit" and body["simulated"] is False
    assert client.post("/api/reports/field-audit", json=audit).status_code == 401


# ---- confirmations -----------------------------------------------------------------------------


def test_still_there_and_fixed_taps_are_counted_once_per_client(client, make_report, db):
    r = make_report(status="verified")
    assert client.post(f"/api/reports/{r.id}/confirmations", json={"still_present": True}).json() == {"still_present": 1, "fixed": 0}
    # The same client changing their mind replaces the earlier tap rather than adding another.
    assert client.post(f"/api/reports/{r.id}/confirmations", json={"still_present": False}).json() == {"still_present": 0, "fixed": 1}
    assert db.scalar(select(func.count()).select_from(ReportConfirmation)) == 1
    listed = client.get("/api/reports").json()[0]
    assert listed["confirmations"] == {"still_present": 0, "fixed": 1}


def test_confirmations_never_change_a_score_or_status(client, make_report, fake_routes):
    r = make_report(status="unverified", lat=MID_A[0], lng=MID_A[1])
    before = client.post("/api/routes/compare", json=COMPARE_BODY).json()["routes"][0]["metrics"]["counted"]
    for _ in range(3):
        client.post(f"/api/reports/{r.id}/confirmations", json={"still_present": True})
    after = client.post("/api/routes/compare", json=COMPARE_BODY).json()["routes"][0]["metrics"]["counted"]
    assert before == after == 0


def test_rejected_or_unknown_reports_cannot_be_confirmed(client, make_report):
    r = make_report(status="rejected")
    assert client.post(f"/api/reports/{r.id}/confirmations", json={"still_present": True}).status_code == 404
    assert client.post("/api/reports/nope/confirmations", json={"still_present": True}).status_code == 404


# ---- route comparison --------------------------------------------------------------------------


def test_with_no_reports_every_route_is_not_enough_data_never_low_concern(client, fake_routes):
    r = client.post("/api/routes/compare", json=COMPARE_BODY)
    assert r.status_code == 200
    body = r.json()
    assert [x["metrics"]["concern_band"] for x in body["routes"]] == ["insufficient", "insufficient"]
    assert all(x["metrics"]["concern_band_text"] == "Not enough data" for x in body["routes"])
    assert "too little evidence" in " ".join(n["text"] for n in body["notes"])
    assert body["stability"] is None and "does not predict crime" in body["disclaimer"]


def test_verified_reports_are_counted_and_unverified_ones_only_shown(client, make_report, fake_routes):
    make_report(status="verified", lat=MID_A[0], lng=MID_A[1])
    make_report(status="unverified", category="obstruction", lat=MID_A[0], lng=MID_A[1] + 0.001)
    a = client.post("/api/routes/compare", json=COMPARE_BODY).json()["routes"][0]
    assert a["metrics"]["counted"] == 1
    assert {e["basis"] for e in a["evidence"]} == {"verified", "unverified"}
    assert a["metrics"]["factors"]["lighting"]["score"] > 0 and a["metrics"]["factors"]["hazard"]["score"] == 0
    assert any(c["observed"] for c in a["chunks"]) and any(not c["observed"] for c in a["chunks"])


def test_one_client_cannot_corroborate_its_own_reports(client, make_report, fake_routes):
    for dlat in (0.0, 0.0001):  # two reports of the same issue from the same client
        assert client.post("/api/reports", json={**REPORT, "lat": MID_A[0] + dlat}).status_code == 201
    a = client.post("/api/routes/compare", json=COMPARE_BODY).json()["routes"][0]
    assert a["metrics"]["counted"] == 0 and {e["basis"] for e in a["evidence"]} == {"unverified"}


def test_two_different_reporters_do_corroborate(client, make_report, fake_routes):
    make_report(lat=MID_A[0], lng=MID_A[1], reporter_id="anon:aaaa")
    make_report(lat=MID_A[0] + 0.0001, lng=MID_A[1], reporter_id="anon:bbbb")
    a = client.post("/api/routes/compare", json=COMPARE_BODY).json()["routes"][0]
    assert a["metrics"]["counted"] == 2 and {e["basis"] for e in a["evidence"]} == {"corroborated"}


def test_persona_and_custom_weights_change_the_weighting(client, make_report, fake_routes):
    make_report(status="verified", lat=MID_A[0], lng=MID_A[1])
    default = client.post("/api/routes/compare", json=COMPARE_BODY).json()["params"]["weight_share_applied"]
    night = client.post("/api/routes/compare", json={**COMPARE_BODY, "persona": "night_walker"}).json()["params"]["weight_share_applied"]
    custom = client.post("/api/routes/compare", json={**COMPARE_BODY, "weights": {"lighting": 1, "hazard": 3}}).json()["params"]["weight_share_applied"]
    assert night["lighting"] > default["lighting"] and custom == {"lighting": 25, "hazard": 75}


def test_daylight_lowers_the_lighting_weight_and_says_so(client, fake_routes):
    day = client.post("/api/routes/compare", json={**COMPARE_BODY, "at": "2026-06-21T12:00:00Z"}).json()
    night = client.post("/api/routes/compare", json=COMPARE_BODY).json()
    assert day["daylight"]["period"] == "day" and night["daylight"]["period"] == "night"
    assert day["params"]["weight_share_applied"]["lighting"] < night["params"]["weight_share_applied"]["lighting"]
    assert "daytime" in " ".join(n["text"] for n in day["notes"])


def test_the_comparison_includes_stability_when_two_routes_have_evidence(client, make_report, fake_routes):
    for i in range(8):
        make_report(status="verified", lat=MID_A[0], lng=-0.0995 + i * 0.0018)
    # Route B gets coverage without issues: resolved reports are observed but not counted.
    for i in range(10):
        make_report(status="resolved", category="obstruction", lat=51.502, lng=-0.0985 + i * 0.0013)
    body = client.post("/api/routes/compare", json=COMPARE_BODY).json()
    assert body["stability"] is not None and body["stability"]["label"] in ("stable", "close call")
    assert client.post("/api/routes/compare", json={**COMPARE_BODY, "sensitivity": False}).json()["stability"] is None


@pytest.mark.parametrize(
    "patch",
    [
        {"persona": "robot"},
        {"origin": {"lat": 51.5}},
        {"origin": {"query": "x", "lat": 1, "lng": 2}},
        {"weights": {"lighting": 0, "hazard": 0}},
        {"weights": {"lighting": 500}},
        {"extra": True},
    ],
)
def test_bad_comparison_requests_are_rejected(client, fake_routes, patch):
    assert client.post("/api/routes/compare", json={**COMPARE_BODY, **patch}).status_code == 422


def test_routing_failures_are_reported_with_a_clear_message(client, monkeypatch):
    async def down(origin, dest, *, client=None):
        raise RoutingError("The routing service could not be reached. Try again in a moment.", 502)

    monkeypatch.setattr(routing, "get_routes", down)
    r = client.post("/api/routes/compare", json=COMPARE_BODY)
    assert r.status_code == 502 and "could not be reached" in r.json()["detail"]


def test_place_names_are_looked_up_server_side(client, fake_routes, monkeypatch):
    seen = []

    async def geo(query, *, client=None):
        seen.append(query)
        return (51.5, -0.1)

    monkeypatch.setattr(routing, "geocode", geo)
    body = {**COMPARE_BODY, "origin": {"query": "Trafalgar Square"}}
    assert client.post("/api/routes/compare", json=body).status_code == 200 and seen == ["Trafalgar Square"]


def test_comparing_routes_writes_nothing_to_the_database(client, make_report, fake_routes, db):
    make_report(status="verified", lat=MID_A[0], lng=MID_A[1])
    before = counts(db)
    for _ in range(3):
        assert client.post("/api/routes/compare", json=COMPARE_BODY).status_code == 200
    assert counts(db) == before  # no searches, origins or destinations are recorded


def test_comparisons_are_rate_limited(client, fake_routes):
    codes = [client.post("/api/routes/compare", json=COMPARE_BODY).status_code for _ in range(61)]
    assert codes[:60] == [200] * 60 and codes[60] == 429


# ---- demo data and the OSM baseline ------------------------------------------------------------


def test_demo_data_is_flagged_hidden_by_default_repeatable_and_removable(client, fake_routes, db):
    first = client.post("/api/admin/demo-seed", json={"origin": {"lat": 51.5, "lng": -0.1}, "destination": {"lat": 51.5, "lng": -0.085}}, headers=ADMIN)
    assert first.status_code == 201 and first.json()["created"] >= 6 and "simulated" in first.json()["note"]
    assert all(r.simulated and r.source == "simulated" for r in db.scalars(select(Report)))

    again = client.post("/api/admin/demo-seed", json={"origin": {"lat": 51.5, "lng": -0.1}, "destination": {"lat": 51.5, "lng": -0.085}}, headers=ADMIN).json()
    assert again["created"] == 0 and again["already_present"] == first.json()["created"]

    assert client.get("/api/reports").json() == []
    assert client.post("/api/routes/compare", json=COMPARE_BODY).json()["routes"][0]["metrics"]["simulated"] is False
    shown = client.post("/api/routes/compare", json={**COMPARE_BODY, "include_simulated": True}).json()
    assert any(r["metrics"]["simulated"] for r in shown["routes"])
    assert "simulated demo reports" in " ".join(n["text"] for n in shown["notes"])

    assert client.delete("/api/admin/demo-seed", headers=ADMIN).json()["deleted"] == first.json()["created"]
    assert counts(db)["Report"] == 0 and counts(db)["ReportEvent"] == 0


def test_demo_seed_needs_the_admin_token(client, fake_routes):
    body = {"origin": {"lat": 51.5, "lng": -0.1}, "destination": {"lat": 51.5, "lng": -0.085}}
    assert client.post("/api/admin/demo-seed", json=body).status_code == 401
    assert client.delete("/api/admin/demo-seed").status_code == 401


def baseline_elements():
    lamps = [{"type": "node", "id": 100 + i, "lat": 51.50003, "lon": -0.1 + i * 0.0007, "tags": {"highway": "street_lamp"}} for i in range(22)]
    helps = [{"type": "node", "id": 200 + i, "lat": 51.5004, "lon": -0.0995 + i * 0.0002, "tags": {"amenity": "pharmacy"}} for i in range(6)]
    shops = [{"type": "node", "id": 300 + i, "lat": 51.5006, "lon": -0.0995 + i * 0.0012, "tags": {"shop": "convenience"}} for i in range(12)]
    return lamps + helps + shops


def test_the_osm_baseline_adds_informational_layers_without_changing_the_score(client, make_report, fake_routes):
    make_report(status="verified", lat=MID_A[0], lng=MID_A[1])
    before = client.post("/api/routes/compare", json=COMPARE_BODY).json()
    assert not before["routes"][0]["metrics"]["informational"]["help"]["available"]

    load = client.post("/api/admin/baseline/load", json={"elements": baseline_elements()}, headers=ADMIN)
    assert load.status_code == 200 and load.json()["by_kind"] == {"lamp": 22, "help": 6, "activity": 12}

    after = client.post("/api/routes/compare", json=COMPARE_BODY).json()
    info = after["routes"][0]["metrics"]["informational"]
    assert info["help"]["available"] and info["lamps"]["available"] and info["activity"]["available"]
    assert all(layer["scored"] is False for layer in info.values())
    assert after["routes"][0]["metrics"]["concern_score"] == before["routes"][0]["metrics"]["concern_score"]
    assert "do not enter the score" in " ".join(n["text"] for n in after["notes"])


def test_baseline_import_reports_overpass_problems_clearly(client, monkeypatch):
    from services import osm_baseline

    async def down(*a, **k):
        raise osm_baseline.BaselineError("Overpass could not be reached.", 502)

    monkeypatch.setattr(osm_baseline, "fetch_overpass", down)
    r = client.post("/api/admin/baseline/import", json={"south": 51.49, "west": -0.11, "north": 51.51, "east": -0.08}, headers=ADMIN)
    assert r.status_code == 502 and "Overpass" in r.json()["detail"]
    assert client.post("/api/admin/baseline/import", json={"south": 0, "west": 0, "north": 1, "east": 1}, headers=ADMIN).status_code in (422, 502)


# ---- dashboard ---------------------------------------------------------------------------------


def test_stats_summarise_real_data_and_warn_about_gaps(client, make_report):
    make_report(status="verified", reported_at=ago(days=20), verified_at=ago(days=10))
    make_report(status="unverified", category="footpath", lat=51.51, reported_at=ago(days=9))
    make_report(status="unverified", category="obstruction", lat=51.52)
    make_report(status="verified", simulated=True, source="simulated", lat=51.53)
    s = client.get("/api/dashboard/stats", headers=ADMIN).json()
    assert s["total"] == 3 and s["simulated"] == 1  # simulated data stays out of every other figure
    assert s["by_status"] == {"verified": 1, "unverified": 2}
    assert s["pending"] == 2 and s["pending_over_7_days"] == 1 and s["oldest_pending_days"] >= 9
    assert s["median_verified_age_days"] == pytest.approx(10, abs=0.5)
    assert s["classifier"]["trained_on_is_synthetic"] is True
    text = " ".join(s["warnings"])
    assert "No OpenStreetMap baseline" in text and "waited more than 7 days" in text
    assert client.get("/api/dashboard/stats").status_code == 401


def test_the_queue_puts_corroborated_and_severe_reports_first(client, make_report):
    lone = make_report(category="streetlight", lat=51.5000, reported_at=ago(days=1, minutes=5))
    pair_a = make_report(category="obstruction", lat=51.5100, reported_at=ago(days=1, minutes=5))
    pair_b = make_report(category="obstruction", lat=51.5101, reported_at=ago(days=1, minutes=5))
    minor = make_report(category="visibility", lat=51.5200, reported_at=ago(days=10))
    make_report(status="unverified", simulated=True, source="simulated", lat=51.5300)
    make_report(status="verified", lat=51.5400)  # not pending
    client.post(f"/api/reports/{lone.id}/confirmations", json={"still_present": True})

    q = client.get("/api/dashboard/queue", headers=ADMIN).json()
    ids = [i["report"]["id"] for i in q]
    assert set(ids) == {lone.id, pair_a.id, pair_b.id, minor.id}  # no simulated, no verified
    assert set(ids[:2]) == {pair_a.id, pair_b.id}
    assert [i["priority"] for i in q] == sorted([i["priority"] for i in q], reverse=True)
    top = q[0]
    assert top["similar_nearby"] == 1 and any("similar report" in r for r in top["reasons"])
    lone_item = next(i for i in q if i["report"]["id"] == lone.id)
    assert any("still there" in r for r in lone_item["reasons"])
    assert client.get("/api/dashboard/queue", headers=ADMIN, params={"limit": 2}).json().__len__() == 2


def test_the_methodology_is_public_and_states_the_limits(client):
    m = client.get("/api/methodology").json()
    assert any("predict crime" in x.lower() for x in m["does_not"])
    assert m["score"]["default_weights"] == {"lighting": 35.0, "hazard": 30.0} and "Provisional" in m["score"]["weights_status"]
    assert set(m["personas"]) == {"default", "night_walker", "step_free", "cyclist"}
    assert m["freshness"]["half_life_days"]["obstruction"] < m["freshness"]["half_life_days"]["streetlight"]
    assert any("neighbourhoods" in x for x in m["fairness"]) and any("not stored" in x for x in m["privacy"])
    assert m["classifier"]["trained_on_is_synthetic"] is True


def test_cors_allows_the_frontend_origin(client):
    r = client.options("/api/reports", headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type"})
    assert r.headers.get("access-control-allow-origin") == "http://localhost:5173"
    bad = client.options("/api/reports", headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "POST"})
    assert "access-control-allow-origin" not in bad.headers
