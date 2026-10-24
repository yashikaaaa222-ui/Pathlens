from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from services import freshness
from services.explanation import explain
from services.geo import chunk_route
from services.risk_engine import (
    CATEGORIES,
    PERSONAS,
    Config,
    FeaturePoint,
    ReportPoint,
    RouteGeom,
    analyse_route,
    effective_weights,
    rank_stability,
)

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)
ROUTE = RouteGeom([(0.0, 0.0), (0.0, 0.009)], 1002.0, 720.0)  # about 1 km along the equator
SHORT = RouteGeom([(0.0, 0.0), (0.0, 0.0027)], 300.0, 216.0)  # about 300 m


def days_ago(d):
    return NOW - timedelta(days=d)


def rep(**kw):
    base = dict(id=f"r{id(kw)}-{len(kw)}", category="streetlight", lat=0.00005, lng=0.0045, status="verified", reported_at=days_ago(10))
    base.update(kw)
    return ReportPoint(**base)


def run(reports, route=ROUTE, **kw):
    return analyse_route(route, reports, NOW, name=kw.pop("name", "Route A"), **kw)


def cover(route=ROUTE, step_lng=0.0008):
    """Resolved reports along the route: they make every chunk 'observed' without counting as issues."""
    n = int(round((route.coords[-1][1] - route.coords[0][1]) / step_lng)) + 1
    return [
        ReportPoint(id=f"cover-{route.distance}-{i}", category="obstruction", lat=0.0, lng=route.coords[0][1] + i * step_lng, status="resolved", reported_at=days_ago(5))
        for i in range(n)
    ]


# ---- rules carried over from the frontend engine -------------------------------------------------


def test_chunks_add_up_to_the_route_length():
    chunks = chunk_route(ROUTE.coords, 100)
    assert 10 <= len(chunks) <= 11
    assert sum(length for _, length in chunks) == pytest.approx(1002, abs=5)


def test_no_evidence_means_not_enough_data_never_low_concern():
    a = run([])
    assert a.metrics.concern_band == "insufficient"
    assert a.metrics.coverage == 0
    assert a.metrics.concern_range is None


def test_a_verified_report_is_counted_and_raises_the_lighting_score():
    m = run([rep()]).metrics
    assert m.counted == 1
    assert m.factors["lighting"].score > 0
    assert m.factors["hazard"].score == 0


def test_a_single_unverified_report_is_shown_but_not_counted():
    a = run([rep(status="unverified")])
    assert a.metrics.counted == 0
    assert len(a.evidence) == 1 and a.evidence[0].basis == "unverified"
    assert a.metrics.coverage > 0


def test_rejected_and_resolved_reports_never_count_and_rejected_gives_no_coverage():
    assert run([rep(status="rejected")]).metrics.counted == 0
    assert run([rep(status="rejected")]).metrics.coverage == 0
    assert run([rep(status="resolved")]).metrics.counted == 0
    assert run([rep(status="resolved")]).metrics.coverage > 0


def test_reports_far_from_the_route_are_ignored():
    assert run([rep(lat=0.01)]).evidence == []


def test_freshness_decays_by_category():
    half = {c: freshness.HALF_LIFE_DAYS[c] for c in ("streetlight", "obstruction")}
    lamp = run([rep(reported_at=days_ago(half["streetlight"]))]).evidence[0].weight
    block = run([rep(category="obstruction", reported_at=days_ago(half["obstruction"]))]).evidence[0].weight
    assert lamp == pytest.approx(0.5, abs=0.01) and block == pytest.approx(0.5, abs=0.01)
    assert run([rep(category="obstruction", reported_at=days_ago(14))]).evidence[0].weight < run([rep(reported_at=days_ago(14))]).evidence[0].weight


# ---- trust tiers ------------------------------------------------------------------------------


def test_two_independent_unverified_reports_corroborate_at_half_weight():
    a = run([rep(id="a", status="unverified", reporter_id="u1"), rep(id="b", status="unverified", reporter_id="u2")])
    assert a.metrics.counted == 2 and all(e.basis == "corroborated" for e in a.evidence)
    full = run([rep(id="a"), rep(id="b")]).metrics.factors["lighting"].raw
    assert a.metrics.factors["lighting"].raw < full


def test_one_person_reporting_twice_is_not_corroboration():
    a = run([rep(id="a", status="unverified", reporter_id="u1"), rep(id="b", status="unverified", reporter_id="u1")])
    assert a.metrics.counted == 0


def test_reports_without_a_reporter_id_count_as_separate_reporters():
    a = run([rep(id="a", status="unverified"), rep(id="b", status="unverified")])
    assert a.metrics.counted == 2


def test_stale_unverified_reports_expire_but_verified_ones_do_not():
    old = run([rep(status="unverified", reported_at=days_ago(90))])
    assert old.evidence == [] and old.metrics.expired_unverified == 1
    assert len(run([rep(status="verified", reported_at=days_ago(90))]).evidence) == 1


# ---- reporting-bias and small-sample fixes ----------------------------------------------------


def test_smoothing_stops_one_report_saturating_a_short_route():
    one = [rep(lng=0.00135)]
    smoothed = run(one, SHORT).metrics.concern_score
    raw = run(one, SHORT, ).metrics  # default config, for the assertion below
    unsmoothed = analyse_route(SHORT, one, NOW, Config(smoothing_km=0.0), name="x").metrics.concern_score
    assert smoothed < unsmoothed
    assert raw.concern_score == smoothed


def test_longer_routes_are_not_penalised_for_length():
    long_route = RouteGeom([(0.0, 0.0), (0.0, 0.018)], 2004.0, 1440.0)
    short = run([rep(lng=0.0045)] + cover()).metrics.concern_score
    long = run([rep(lng=0.0045)] + cover(long_route), long_route).metrics.concern_score
    assert long < short  # the same single issue is diluted over twice the distance


def test_range_extrapolates_the_observed_density_to_the_unobserved_part():
    # Issues sit only at the start, so the rest of the route is unobserved.
    start = [rep(id=f"s{i}", lng=0.0003 + i * 0.0001) for i in range(4)]
    m = run(start).metrics
    assert m.coverage < 0.5
    low, high = m.concern_range
    assert low == m.concern_score and high > low


def test_range_collapses_when_the_whole_route_is_observed():
    m = run(cover()).metrics
    assert m.coverage == pytest.approx(1.0, abs=0.01)
    assert m.concern_range == (m.concern_score, m.concern_score)


def test_longest_stretch_reports_the_longest_run_of_counted_issues():
    m = run([rep(id=f"s{i}", lng=0.0005 + i * 0.0009) for i in range(3)]).metrics
    assert m.longest_stretch_m >= 100


# ---- personas, custom weights and time of day --------------------------------------------------


def test_personas_shift_the_weights():
    default = run([rep()]).metrics.weight_share
    night = run([rep()], weights=PERSONAS["night_walker"].weights).metrics.weight_share
    assert night["lighting"] > default["lighting"]
    assert sum(night.values()) in (99, 100, 101)


def test_custom_weights_are_normalised_and_zero_weights_fall_back_to_the_default():
    w = effective_weights({"lighting": 0, "hazard": 0})
    assert w["lighting"] > 0 and w["hazard"] > 0
    assert run([rep()], weights={"lighting": 3, "hazard": 1}).metrics.weight_share == {"lighting": 75, "hazard": 25}


def test_daylight_lowers_the_lighting_share():
    night = run([rep()], lighting_multiplier=1.0).metrics.weight_share["lighting"]
    day = run([rep()], lighting_multiplier=0.25).metrics.weight_share["lighting"]
    assert day < night


def test_a_persona_can_boost_one_category():
    plain = run([rep(category="footpath")]).evidence[0].weight
    boosted = run([rep(category="footpath")], severity_boost=PERSONAS["step_free"].severity_boost).evidence[0].weight
    assert boosted == pytest.approx(plain * 1.5, rel=0.01)


# ---- sensitivity ------------------------------------------------------------------------------


def two_routes(a_issues: int, b_issues: int):
    a_reports = cover() + [rep(id=f"a{i}", lng=0.0005 + i * 0.0006) for i in range(a_issues)]
    b_route = RouteGeom([(0.001, 0.0), (0.001, 0.009)], 1002.0, 720.0)
    b_reports = [replace(r, id="b" + r.id, lat=0.001) for r in cover(b_route)] + [
        rep(id=f"b{i}", lat=0.001, lng=0.0005 + i * 0.0006) for i in range(b_issues)
    ]
    return [run(a_reports, name="Route A"), run(b_reports, b_route, name="Route B")]


def test_a_clear_winner_is_stable_under_weight_changes():
    s = rank_stability(two_routes(8, 0))
    assert s.stable and s.label == "stable" and s.best_share["Route B"] >= 0.8


def test_equal_routes_are_a_close_call():
    s = rank_stability(two_routes(3, 3))
    assert not s.stable and s.label == "close call" and s.ties > 0


def test_stability_is_deterministic_and_needs_two_usable_routes():
    assert rank_stability(two_routes(5, 1)) == rank_stability(two_routes(5, 1))
    assert rank_stability([run([])]) is None


# ---- informational layers never touch the score ------------------------------------------------


def feature_set():
    lamps = [FeaturePoint("lamp", 0.00003, i * 0.00045) for i in range(21)]
    help_points = [FeaturePoint("help", 0.0001, 0.0001 * i) for i in range(1, 7)]
    activity = [FeaturePoint("activity", 0.0002, 0.0007 * i) for i in range(1, 12)]
    return lamps + help_points + activity


def test_layers_are_unavailable_without_enough_mapped_data():
    info = run([rep()]).metrics.informational
    assert not any(layer["available"] for layer in info.values())
    sparse = run([rep()], features=[FeaturePoint("help", 0, 0)] * 3).metrics.informational
    assert sparse["help"]["available"] is False


def test_layers_report_gaps_and_never_change_the_score():
    reports = [rep(), rep(id="b", lng=0.002)]
    plain = run(reports).metrics
    with_layers = run(reports, features=feature_set()).metrics
    info = with_layers.informational
    assert info["help"]["available"] and 450 <= info["help"]["gap_m"] <= 560
    assert info["lamps"]["available"] and info["lamps"]["per_km"] > 10
    assert info["activity"]["available"]
    assert all(layer["scored"] is False for layer in info.values())
    assert with_layers.concern_score == plain.concern_score
    assert with_layers.factors == plain.factors


# ---- explanations -----------------------------------------------------------------------------


def text(notes):
    return " ".join(n["text"] for n in notes)


def test_explanations_state_the_caveat_and_flag_simulated_data():
    sim = [replace(r, simulated=True) for r in [rep(id=f"x{i}", lng=0.001 + i * 0.002) for i in range(4)]]
    notes = text(explain([run(sim)]))
    assert "simulated" in notes and "unknown, not clear" in notes


def test_explanations_name_insufficient_routes_and_do_not_call_them_clear():
    notes = text(explain([run([], name="Route A")]))
    assert "too little evidence" in notes and "does not mean it is free of problems" in notes


def test_explanations_report_close_calls_and_daylight():
    analyses = two_routes(3, 3)
    notes = text(explain(analyses, stability=rank_stability(analyses), daylight={"period": "day"}))
    assert "close call" in notes and "daytime" in notes


def test_categories_cover_every_freshness_half_life():
    assert set(CATEGORIES) == set(freshness.HALF_LIFE_DAYS)
