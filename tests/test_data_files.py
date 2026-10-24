"""The sample data in data/ and the script that loads it.

These files are synthetic on purpose. The tests make sure they stay that way (labelled, reproducible, never
mistaken for real data) and that they work with the real backend code.
"""
import csv
import json
import os

import pytest

import build_synthetic as build
import load_data
from ai.seed_reports import SEED
from conftest import ADMIN
from models.report import Report
from schemas.report import FieldAuditCreate
from services.geo import haversine
from services.osm_baseline import parse_overpass
from services.risk_engine import CATEGORIES, FeaturePoint, ReportPoint, RouteGeom, analyse_route

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def read_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


# ---- the files are labelled, complete and reproducible -------------------------------------------------


def test_committed_files_match_what_the_builder_makes():
    for path, text in build.build().items():
        with open(path, newline="", encoding="utf-8") as f:
            assert f.read() == text, f"{os.path.relpath(path, ROOT)} is out of date: run python data/build_synthetic.py"


def test_every_road_file_says_it_is_synthetic():
    corridor = read_json(build.PATHS["corridor"])
    assert corridor["note"].startswith("SYNTHETIC SAMPLE")
    assert all(f["properties"]["synthetic"] is True for f in corridor["features"])
    snapshot = read_json(build.PATHS["snapshot"])
    assert "NOT OpenStreetMap" in snapshot["generator"] and snapshot["osm3s"]["copyright"].startswith("None")
    assert all(e["id"] < 0 and e["tags"]["note"] == "synthetic sample" for e in snapshot["elements"])


def test_the_two_routes_are_about_as_long_as_they_say():
    for f in read_json(build.PATHS["corridor"])["features"]:
        if f["geometry"]["type"] != "LineString":
            continue
        pts = [(lat, lng) for lng, lat in f["geometry"]["coordinates"]]
        length = sum(haversine(a, b) for a, b in zip(pts, pts[1:]))
        assert length == pytest.approx(f["properties"]["distance_m"], rel=0.12), f["properties"]["name"]


def test_the_seed_csv_is_the_classifiers_training_set():
    rows = read_csv(build.PATHS["seed"])
    assert [(r["text"], r["category"]) for r in rows] == SEED
    assert {r["category"] for r in rows} == set(CATEGORIES)


def test_simulated_reports_are_all_labelled_simulated():
    rows = read_csv(build.PATHS["simulated"])
    assert len(rows) >= 6
    assert all(r["simulated"] == "true" and r["source"] == "simulated" and r["description"] == "Simulated demo report" for r in rows)
    assert all(r["category"] in CATEGORIES and r["status"] in {"verified", "unverified", "resolved", "rejected"} for r in rows)
    assert len({r["id"] for r in rows}) == len(rows)


def test_the_samples_work_together_in_the_scoring_engine():
    from datetime import datetime, timezone

    coords = {f["properties"]["name"]: [(lat, lng) for lng, lat in f["geometry"]["coordinates"]] for f in read_json(build.PATHS["corridor"])["features"] if f["geometry"]["type"] == "LineString"}
    route = RouteGeom(coords["Route A"], 1040.0, 750.0)
    reports = [
        ReportPoint(r["id"], r["category"], float(r["lat"]), float(r["lng"]), r["status"], datetime.fromisoformat(r["reported_at"]),
                    datetime.fromisoformat(r["verified_at"]) if r["verified_at"] else None, simulated=True)
        for r in read_csv(build.PATHS["simulated"])
    ]
    feats = [FeaturePoint(f["kind"], f["lat"], f["lng"]) for f in parse_overpass(read_json(build.PATHS["snapshot"]))]
    a = analyse_route(route, reports, datetime(2026, 10, 1, tzinfo=timezone.utc), features=feats)
    assert a.metrics.concern_band in {"low", "moderate", "elevated", "insufficient"}
    layers = a.metrics.informational
    assert all(layers[k]["available"] for k in ("help", "lamps", "activity"))  # the snapshot is big enough for every layer
    assert all(layers[k]["scored"] is False for k in layers)  # and none of it enters the score


# ---- the loader script ---------------------------------------------------------------------------------


def test_baseline_snapshot_loads_through_the_api_and_loading_twice_changes_nothing(client):
    n = len(read_json(build.PATHS["snapshot"])["elements"])
    first = load_data.load_baseline(client, build.PATHS["snapshot"], ADMIN)
    assert first["added"] == n and first["updated"] == 0 and first["by_kind"] == {"lamp": 11, "help": 5, "activity": 6}
    second = load_data.load_baseline(client, build.PATHS["snapshot"], ADMIN)
    assert second["added"] == 0 and second["updated"] == n


def test_loading_the_snapshot_lights_up_the_context_layers_in_a_comparison(client, monkeypatch):
    from services import routing

    corridor = {f["properties"]["name"]: f for f in read_json(build.PATHS["corridor"])["features"] if f["geometry"]["type"] == "LineString"}

    async def fake_routes(origin, dest, *, client=None):
        return [RouteGeom([(lat, lng) for lng, lat in corridor["Route A"]["geometry"]["coordinates"]], 1040.0, 750.0, "stub")]

    monkeypatch.setattr(routing, "get_routes", fake_routes)
    body = {"origin": {"lat": 51.5, "lng": -0.1}, "destination": {"lat": 51.5, "lng": -0.085}}
    before = client.post("/api/routes/compare", json=body).json()["routes"][0]["metrics"]["informational"]
    assert not any(layer["available"] for layer in before.values())
    load_data.load_baseline(client, build.PATHS["snapshot"], ADMIN)
    after = client.post("/api/routes/compare", json=body).json()["routes"][0]["metrics"]["informational"]
    assert all(layer["available"] for layer in after.values())


def test_the_loader_refuses_a_file_that_is_not_an_overpass_response(client, tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text('{"type": "FeatureCollection"}')
    with pytest.raises(SystemExit, match="elements"):
        load_data.load_baseline(client, str(bad), ADMIN)


def test_the_field_audit_template_matches_what_the_api_accepts():
    header = read_csv_header(os.path.join(ROOT, "data", "reports", "field_audit_template.csv"))
    assert header == load_data.FIELD_AUDIT_COLUMNS
    assert set(header) == set(FieldAuditCreate.model_fields)


def read_csv_header(path):
    with open(path, newline="", encoding="utf-8") as f:
        return next(csv.reader(f))


def test_field_audit_rows_are_saved_as_verified_and_bad_rows_are_skipped_not_fatal(client, db_session, tmp_path):
    sheet = tmp_path / "audit.csv"
    sheet.write_text(
        "category,lat,lng,description,auditor\n"
        "streetlight,51.5000,-0.0950,Lamp out at the crossing,A1\n"
        "obstruction,51.5001,-0.0940,,A1\n"
        ",,,,\n"                                              # blank row: ignored, not an error
        "footpath,north,-0.0930,Tiles missing,A1\n"           # bad number
        "pothole,51.5002,-0.0920,Not a category,A1\n"         # category the API does not know
        "streetlight,51.5003,-0.0910,call 98765 43210,A1\n"   # personal details in text
    )
    result = load_data.load_field_audit(client, str(sheet), ADMIN)
    assert result["saved"] == 2
    assert [n for n, _ in result["skipped"]] == [5, 6, 7]
    assert "numbers" in result["skipped"][0][1] and "phone" in result["skipped"][2][1].lower()
    rows = db_session.query(Report).all()
    assert len(rows) == 2 and all(r.status == "verified" and r.source == "field_audit" and not r.simulated for r in rows)


def test_the_loader_stops_at_once_when_the_credentials_are_wrong(client, tmp_path):
    sheet = tmp_path / "audit.csv"
    sheet.write_text("category,lat,lng\nstreetlight,51.5,-0.09\nstreetlight,51.5,-0.091\n")
    with pytest.raises(SystemExit, match="refused the admin credentials"):
        load_data.load_field_audit(client, str(sheet), {"X-Admin-Token": "wrong"})


def test_the_loader_needs_the_three_essential_columns(client, tmp_path):
    sheet = tmp_path / "audit.csv"
    sheet.write_text("category,description\nstreetlight,x\n")
    with pytest.raises(SystemExit, match="missing column"):
        load_data.load_field_audit(client, str(sheet), ADMIN)


def test_credentials_come_from_the_flags_or_the_environment(monkeypatch):
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    assert load_data.auth_headers() == {}
    monkeypatch.setenv("ADMIN_TOKEN", "from-env")
    assert load_data.auth_headers() == {"X-Admin-Token": "from-env"}
    assert load_data.auth_headers("flag") == {"X-Admin-Token": "flag"}
    assert load_data.auth_headers("flag", "jwt") == {"Authorization": "Bearer jwt"}


def test_real_report_files_cannot_be_committed_by_accident():
    ignore = open(os.path.join(ROOT, "data", "reports", ".gitignore")).read().splitlines()
    assert "*" in ignore and "!field_audit_template.csv" in ignore and "!README.md" in ignore
