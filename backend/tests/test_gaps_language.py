"""Information gaps (where nothing is known) and the wording rule (no alarm words in anything the product says)."""
import re
from datetime import datetime, timedelta, timezone

import pytest

from services.explanation import explain
from services.risk_engine import BAND_TEXT, CATEGORIES, PERSONAS, Config, FeaturePoint, ReportPoint, RouteGeom, analyse_route

from conftest import ADMIN, COMPARE_BODY, MID_A

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)
ROUTE = RouteGeom([(0.0, 0.0), (0.0, 0.009)], 1002.0, 720.0)  # about 1 km, due east along the equator


def rp(lng, status="verified", cat="streetlight", i=[0]):
    i[0] += 1
    return ReportPoint(id=f"g{i[0]}", category=cat, lat=0.00005, lng=lng, status=status, reported_at=NOW - timedelta(days=10))


# ---- information gaps ------------------------------------------------------------------------------


def test_a_stretch_with_no_recent_evidence_is_listed_as_a_gap():
    a = analyse_route(ROUTE, [rp(0.0005)], NOW)  # one report near the start observes only the first ~200 m
    assert len(a.gaps) == 1
    g = a.gaps[0]
    assert 195 <= g.from_m <= 205 and 995 <= g.to_m <= 1005 and 790 <= g.length_m <= 810
    assert g.lat == pytest.approx(0.0, abs=0.001) and 0.0018 < g.lng < 0.009  # a marker inside the gap


def test_a_fully_covered_route_has_no_gaps():
    cover = [rp(i * 0.0008, status="resolved", cat="obstruction") for i in range(12)]  # resolved: observed, not counted
    assert analyse_route(ROUTE, cover, NOW).gaps == []


def test_gaps_are_listed_longest_first_and_short_ones_are_ignored():
    reports = [rp(0.0003), rp(0.0050)]  # observe chunks 0-1 and 4-6, leaving a 200 m and a 302 m gap
    a = analyse_route(ROUTE, reports, NOW, Config(gap_min_m=150.0))
    assert [g.length_m for g in a.gaps] == pytest.approx([302, 200], abs=3)
    assert a.gaps[0].from_m > a.gaps[1].from_m  # the longest gap is the later one here
    assert analyse_route(ROUTE, reports, NOW, Config(gap_min_m=400.0)).gaps == []


def test_a_route_with_no_evidence_is_one_big_gap_and_still_not_enough_data():
    a = analyse_route(ROUTE, [], NOW)
    assert a.metrics.concern_band == "insufficient" and len(a.gaps) == 1 and a.gaps[0].length_m >= 1000


def test_the_comparison_response_lists_gaps_and_the_explanation_says_where_evidence_is_missing(client, make_report, fake_routes):
    make_report(status="verified", lat=MID_A[0], lng=MID_A[1])  # the middle of route A: about 300 m observed
    body = client.post("/api/routes/compare", json=COMPARE_BODY).json()
    a, b = body["routes"]
    assert a["metrics"]["concern_band"] != "insufficient" and len(a["gaps"]) == 2
    assert a["gaps"][0]["length_m"] >= a["gaps"][1]["length_m"] >= 200
    assert set(a["gaps"][0]) == {"from_m", "to_m", "length_m", "lat", "lng"}
    assert b["metrics"]["concern_band"] == "insufficient" and len(b["gaps"]) == 1  # gaps are listed even where nothing is known
    text = " ".join(n["text"] for n in body["notes"])
    assert "Gaps of 200 m or more: Route A has 2 stretches with no recent evidence" in text
    assert "Route B has" not in text.split("Gaps of 200 m")[1]  # a route with no evidence is not compared, so no gap note for it


# ---- the wording rule ----------------------------------------------------------------------------------

ALARM = re.compile(r"\b(safe|unsafe|danger\w*|risk\w*|crime\w*|threat\w*|hazardous)\b|\bsafety\b", re.I)


def alarming(texts):
    return [t for t in texts if ALARM.search(t)]


def response_texts(body):
    texts = [n["text"] for n in body["notes"]]
    for r in body["routes"]:
        texts.append(r["metrics"]["concern_band_text"])
        texts += [layer["note"] for layer in r["metrics"]["informational"].values()]
    return texts


def test_the_fixed_vocabulary_has_no_alarm_words():
    vocab = [*BAND_TEXT.values(), *(c.label for c in CATEGORIES.values()), *(p.label for p in PERSONAS.values()), *(p.description for p in PERSONAS.values())]
    assert alarming(vocab) == []


def test_alarm_words_are_detected_so_the_rule_is_not_vacuous():
    assert alarming(["This route is safe", "A dangerous stretch", "Crime rate", "Low risk", "Your safety"]) == [
        "This route is safe", "A dangerous stretch", "Crime rate", "Low risk", "Your safety",
    ]
    assert alarming(["Low reported concern", "Broken streetlight", "Road hazards"]) == []


def test_no_explanation_or_label_uses_alarm_words_in_any_scenario(client, make_report, fake_routes):
    scenarios = [dict(COMPARE_BODY)]  # nothing known at all
    # Evidence along route A (51.5000) and, less of it, along route B (51.5020).
    for i in range(8):
        make_report(status="verified", lat=51.5000, lng=-0.0990 + i * 0.0017, category=("streetlight", "footpath", "obstruction")[i % 3], reported_at=datetime.now(timezone.utc) - timedelta(days=3 * i))
    for i in range(3):
        make_report(status="verified", lat=51.5020, lng=-0.0970 + i * 0.004, category="poor_lighting", reported_at=datetime.now(timezone.utc) - timedelta(days=20))
    make_report(status="unverified", lat=51.5000, lng=-0.0800)
    scenarios += [
        dict(COMPARE_BODY),  # now with evidence
        {**COMPARE_BODY, "at": "2026-12-15T12:00:00Z"},  # daytime
        {**COMPARE_BODY, "persona": "step_free"},
        {**COMPARE_BODY, "persona": "cyclist", "weights": {"lighting": 10, "hazard": 90}},
        {**COMPARE_BODY, "sensitivity": False},
    ]
    assert client.post("/api/admin/demo-seed", json={"origin": COMPARE_BODY["origin"], "destination": COMPARE_BODY["destination"]}, headers=ADMIN).status_code == 201
    scenarios.append({**COMPARE_BODY, "include_simulated": True})

    seen = []
    for body in scenarios:
        resp = client.post("/api/routes/compare", json=body)
        assert resp.status_code == 200
        seen += response_texts(resp.json())
    assert len(seen) > 25  # the scan really saw explanations, not an empty list
    assert alarming(seen) == []


def test_the_informational_layer_notes_have_no_alarm_words():
    near = lambda lng: (0.0001, lng)  # noqa: E731
    feats = [FeaturePoint(k, *near(0.001 * (i + 1))) for k in ("help", "lamp", "activity") for i in range(6)]
    a = analyse_route(ROUTE, [rp(0.0045)], NOW, features=feats)
    texts = [layer["note"] for layer in a.metrics.informational.values()]
    assert all(layer["available"] for layer in a.metrics.informational.values()) and alarming(texts) == []
    assert alarming([n["text"] for n in explain([a])]) == []
