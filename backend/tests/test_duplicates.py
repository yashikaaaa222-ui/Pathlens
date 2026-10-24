from datetime import datetime, timedelta, timezone

from ai.duplicate_detection import Candidate, duplicate_map, find_matches

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)


def cand(id, category="streetlight", lat=0.0, lng=0.0, days=0, text=""):
    return Candidate(id, category, lat, lng, NOW - timedelta(days=days), text)


def test_same_category_close_and_recent_is_a_likely_duplicate():
    m = find_matches(cand("a"), [cand("b", lat=0.0002), cand("c", lat=0.01), cand("d", category="obstruction")])
    assert [x.id for x in m] == ["b"]
    assert 20 < m[0].distance_m < 25


def test_reports_far_apart_in_time_are_not_duplicates():
    assert find_matches(cand("a", days=1), [cand("b", days=90)]) == []


def test_a_report_is_never_its_own_duplicate():
    assert find_matches(cand("a"), [cand("a")]) == []


def test_text_similarity_ranks_matches_but_never_removes_them():
    target = cand("t", text="street light is not working near the bus stop")
    others = [
        cand("far-text", lat=0.0001, text="completely different words about potholes"),
        cand("same-text", lat=0.0003, text="street light not working near bus stop"),
        cand("no-text", lat=0.0002, text=""),
    ]
    m = find_matches(target, others)
    assert {x.id for x in m} == {"far-text", "same-text", "no-text"}  # all within 40 m, so all kept
    assert m[0].id == "same-text"
    assert next(x for x in m if x.id == "no-text").text_similarity is None


def test_duplicate_map_is_symmetric():
    d = duplicate_map([cand("a"), cand("b", lat=0.0002), cand("c", lat=0.05)])
    assert d == {"a": ["b"], "b": ["a"]}
