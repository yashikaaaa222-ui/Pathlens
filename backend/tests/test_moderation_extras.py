"""Consent, accountable moderation, disputes, retention, the civic export and old-database upgrades."""
import csv
import io
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, func, inspect, select, text

import database
from models.report import Report
from services import retention
from services.retention import RetentionPolicy

from conftest import ADMIN, MID_A

REPORT = {"category": "streetlight", "lat": MID_A[0], "lng": MID_A[1], "description": "Light is out near the corner", "consent": True}


def ago(**kw):
    return datetime.now(timezone.utc) - timedelta(**kw)


def from_ip(ip):
    """A different anonymous client (needs TRUST_PROXY=1)."""
    return {"x-forwarded-for": ip}


@pytest.fixture
def proxy(monkeypatch):
    monkeypatch.setenv("TRUST_PROXY", "1")


# ---- consent -------------------------------------------------------------------------------------


@pytest.mark.parametrize("patch", [{}, {"consent": False}])
def test_a_report_without_consent_is_refused_and_nothing_is_saved(client, db, patch):
    body = {k: v for k, v in REPORT.items() if k != "consent"}
    r = client.post("/api/reports", json={**body, **patch})
    assert r.status_code == 422 and "privacy notice" in r.text
    assert db.scalar(select(func.count()).select_from(Report)) == 0


def test_a_null_consent_is_refused_too(client):
    assert client.post("/api/reports", json={**REPORT, "consent": None}).status_code == 422


def test_a_report_with_consent_is_saved(client):
    assert client.post("/api/reports", json=REPORT).status_code == 201


# ---- accountable moderation ----------------------------------------------------------------------


def test_rejecting_needs_a_reason_and_the_reason_is_kept(client, make_report):
    r = make_report()
    assert client.patch(f"/api/reports/{r.id}/status", json={"status": "rejected"}, headers=ADMIN).status_code == 422
    assert client.patch(f"/api/reports/{r.id}/status", json={"status": "rejected", "note": "   "}, headers=ADMIN).status_code == 422
    ok = client.patch(f"/api/reports/{r.id}/status", json={"status": "rejected", "note": "Not on a public road"}, headers=ADMIN)
    assert ok.status_code == 200
    assert client.get(f"/api/reports/{r.id}/events", headers=ADMIN).json()[-1]["note"] == "Not on a public road"


def test_resolving_records_when_and_reopening_clears_it(client, make_report):
    r = make_report(status="verified")
    done = client.patch(f"/api/reports/{r.id}/status", json={"status": "resolved", "note": "Repaired by the ward office"}, headers=ADMIN).json()
    assert done["resolved_at"] is not None
    assert client.get("/api/reports", params={"status": "resolved"}).json()[0]["resolved_at"] is not None
    reopened = client.patch(f"/api/reports/{r.id}/status", json={"status": "unverified"}, headers=ADMIN).json()
    assert reopened["resolved_at"] is None and reopened["verified_at"] is None


def test_the_dashboard_reports_how_long_fixes_take(client, make_report):
    make_report(status="resolved", reported_at=ago(days=12), verified_at=ago(days=10), resolved_at=ago(days=2), lat=51.50)
    make_report(status="resolved", reported_at=ago(days=30), verified_at=ago(days=20), resolved_at=ago(days=10), lat=51.51)
    s = client.get("/api/dashboard/stats", headers=ADMIN).json()
    assert s["median_days_to_resolve"] == pytest.approx(9.0, abs=0.2)  # median of 8 and 10


# ---- disputes and "fixed" votes ------------------------------------------------------------------


def test_anyone_can_dispute_a_report_once(client, make_report):
    r = make_report(status="verified")
    ok = client.post(f"/api/reports/{r.id}/disputes", json={"reason": "This light was fixed last week"})
    assert ok.status_code == 201 and ok.json() == {"disputes": 1}
    assert client.post(f"/api/reports/{r.id}/disputes", json={"reason": "Still wrong, I promise"}).status_code == 409


def test_disputes_from_different_people_add_up(client, proxy, make_report):
    r = make_report(status="verified")
    counts = [client.post(f"/api/reports/{r.id}/disputes", json={"reason": "Not a real problem"}, headers=from_ip(ip)).json()["disputes"] for ip in ("10.0.0.1", "10.0.0.2")]
    assert counts == [1, 2]


@pytest.mark.parametrize("reason", ["no", "x" * 281])
def test_a_dispute_needs_a_real_reason(client, make_report, reason):
    r = make_report(status="verified")
    assert client.post(f"/api/reports/{r.id}/disputes", json={"reason": reason}).status_code == 422


def test_rejected_or_unknown_reports_cannot_be_disputed(client, make_report):
    r = make_report(status="rejected")
    assert client.post(f"/api/reports/{r.id}/disputes", json={"reason": "Wrong place"}).status_code == 404
    assert client.post("/api/reports/nope/disputes", json={"reason": "Wrong place"}).status_code == 404


def test_a_disputed_verified_report_goes_to_the_top_of_the_queue_and_keeps_counting(client, make_report, fake_routes):
    verified = make_report(status="verified", lat=MID_A[0], lng=MID_A[1])
    pending = make_report(category="obstruction", lat=51.52, lng=-0.09)
    before = client.post("/api/routes/compare", json={"origin": {"lat": 51.5, "lng": -0.1}, "destination": {"lat": 51.5, "lng": -0.085}, "at": "2026-12-15T22:00:00Z"}).json()["routes"][0]["metrics"]["counted"]
    client.post(f"/api/reports/{verified.id}/disputes", json={"reason": "I live here and this is wrong"})

    q = client.get("/api/dashboard/queue", headers=ADMIN).json()
    assert [i["report"]["id"] for i in q] == [verified.id, pending.id]
    assert q[0]["kind"] == "flagged" and q[0]["report"]["disputes"] == 1 and any("dispute" in r for r in q[0]["reasons"])
    assert q[1]["kind"] == "pending"
    # A dispute asks a human to look. It does not change the score by itself.
    after = client.post("/api/routes/compare", json={"origin": {"lat": 51.5, "lng": -0.1}, "destination": {"lat": 51.5, "lng": -0.085}, "at": "2026-12-15T22:00:00Z"}).json()["routes"][0]["metrics"]["counted"]
    assert before == after == 1
    assert client.get("/api/dashboard/stats", headers=ADMIN).json()["disputed"] == 1


def test_two_people_saying_it_is_fixed_flags_a_verified_report(client, proxy, make_report):
    r = make_report(status="verified")
    client.post(f"/api/reports/{r.id}/confirmations", json={"still_present": False}, headers=from_ip("10.0.0.1"))
    assert client.get("/api/dashboard/queue", headers=ADMIN).json() == []  # one voice is not enough
    client.post(f"/api/reports/{r.id}/confirmations", json={"still_present": False}, headers=from_ip("10.0.0.2"))
    q = client.get("/api/dashboard/queue", headers=ADMIN).json()
    assert len(q) == 1 and q[0]["kind"] == "flagged" and any("say it is fixed" in x for x in q[0]["reasons"])


def test_a_dispute_also_raises_a_pending_report(client, make_report):
    quiet = make_report(lat=51.50)
    disputed = make_report(lat=51.51)
    client.post(f"/api/reports/{disputed.id}/disputes", json={"reason": "Wrong location"})
    q = client.get("/api/dashboard/queue", headers=ADMIN).json()
    assert q[0]["report"]["id"] == disputed.id and q[1]["report"]["id"] == quiet.id


# ---- retention -----------------------------------------------------------------------------------


def test_the_retention_rules_remove_old_data_and_keep_the_rest(client, make_report, db):
    old_rejected = make_report(status="rejected", updated_at=ago(days=40), lat=51.50)
    new_rejected = make_report(status="rejected", updated_at=ago(days=5), lat=51.51)
    never_reviewed = make_report(status="unverified", reported_at=ago(days=200), lat=51.52)
    recent_pending = make_report(status="unverified", reported_at=ago(days=100), lat=51.53)
    simulated = make_report(status="rejected", simulated=True, source="simulated", updated_at=ago(days=400), lat=51.54)
    old_resolved = make_report(status="resolved", has_photo=True, photo=b"jpeg-bytes", updated_at=ago(days=40), lat=51.55)
    fresh_resolved = make_report(status="resolved", has_photo=True, photo=b"jpeg-bytes", updated_at=ago(days=3), lat=51.56)
    verified = make_report(status="verified", reported_at=ago(days=400), lat=51.57)
    kept = {new_rejected.id, recent_pending.id, simulated.id, old_resolved.id, fresh_resolved.id, verified.id}
    old_resolved_id, fresh_resolved_id = old_resolved.id, fresh_resolved.id  # read before the rows are deleted

    assert client.post("/api/admin/maintenance").status_code == 401
    done = client.post("/api/admin/maintenance", headers=ADMIN).json()
    assert (done["rejected_deleted"], done["unreviewed_deleted"], done["photos_removed"]) == (1, 1, 1)

    db.expire_all()
    assert {r.id for r in db.scalars(select(Report))} == kept  # only the two old rows are gone
    assert db.get(Report, old_resolved_id).has_photo is False and db.get(Report, old_resolved_id).photo is None
    assert db.get(Report, fresh_resolved_id).has_photo is True  # too recent to remove


def test_deleting_a_report_deletes_its_trail_and_votes_with_it(client, make_report, db):
    r = make_report(status="rejected", updated_at=ago(days=40))
    client.post(f"/api/reports/{r.id}/disputes", json={"reason": "Wrong place for sure"})
    assert retention.purge(db)["rejected_deleted"] == 1
    for table in ("report_events", "report_disputes", "report_confirmations"):
        assert db.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar() == 0


def test_the_retention_period_can_be_changed(make_report, db):
    make_report(status="rejected", updated_at=ago(days=10))
    assert retention.purge(db, policy=RetentionPolicy(rejected_days=30, unverified_days=180, photo_days=30))["rejected_deleted"] == 0
    assert retention.purge(db, policy=RetentionPolicy(rejected_days=7, unverified_days=180, photo_days=30))["rejected_deleted"] == 1


def test_the_methodology_states_the_retention_consent_and_photo_rules(client, monkeypatch):
    m = client.get("/api/methodology").json()
    text_ = " ".join(m["privacy"])
    assert "30 days after rejection" in text_ and "180 days" in text_ and "consent" in text_ and "Only moderators can see them" in text_
    assert any("not stored" in x for x in m["privacy"])  # the route-privacy promise is still there
    assert m["accounts"]["login_required_to_report"] is False
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "x" * 40)
    assert client.get("/api/methodology").json()["accounts"]["login_required_to_report"] is True
    monkeypatch.setenv("RETENTION_REJECTED_DAYS", "14")
    assert "14 days after rejection" in " ".join(client.get("/api/methodology").json()["privacy"])


# ---- the civic export ------------------------------------------------------------------------------


def seed_export(make_report):
    make_report(status="verified", reported_at=ago(days=20), verified_at=ago(days=18), description="Lamp out at the gate", lat=51.50)
    make_report(status="resolved", reported_at=ago(days=30), verified_at=ago(days=28), resolved_at=ago(days=8), category="footpath", lat=51.51)
    make_report(status="unverified", lat=51.52)
    make_report(status="rejected", lat=51.53)
    make_report(status="verified", simulated=True, source="simulated", lat=51.54)


def test_the_export_lists_verified_and_resolved_issues_with_how_long_they_have_been_open(client, make_report):
    seed_export(make_report)
    assert client.get("/api/dashboard/export").status_code == 401
    out = client.get("/api/dashboard/export", headers=ADMIN).json()
    assert out["count"] == 2
    by_status = {i["status"]: i for i in out["issues"]}
    assert by_status["verified"]["days_open"] == pytest.approx(18, abs=0.2)
    assert by_status["resolved"]["days_open"] == pytest.approx(20, abs=0.2)
    assert not any(k in by_status["verified"] for k in ("reporter_id", "photo", "has_photo", "classifier_category"))
    assert [i["status"] for i in client.get("/api/dashboard/export", headers=ADMIN, params={"status": "resolved"}).json()["issues"]] == ["resolved"]


def test_the_csv_export_is_safe_to_open_in_a_spreadsheet(client, make_report):
    make_report(status="verified", description="=HYPERLINK(\"http://x\",\"click\")", lat=51.50)
    make_report(status="verified", description="+1+1", lat=51.51)
    make_report(status="verified", description="Lamp out at the gate", lat=51.52)
    r = client.get("/api/dashboard/export", headers=ADMIN, params={"format": "csv"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv") and "attachment" in r.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(r.text)))
    assert {row["description"] for row in rows} == {"'=HYPERLINK(\"http://x\",\"click\")", "'+1+1", "Lamp out at the gate"}
    assert list(rows[0].keys())[:3] == ["id", "category", "label"]


def test_an_empty_csv_export_is_still_valid(client):
    r = client.get("/api/dashboard/export", headers=ADMIN, params={"format": "csv"})
    assert r.status_code == 200 and r.text.strip() == "id"


# ---- upgrading an older database file ----------------------------------------------------------------


def test_columns_added_in_newer_versions_are_added_to_an_old_database():
    old = create_engine("sqlite://")
    with old.begin() as conn:  # the shape of the table before photos, resolved_at and disputes existed
        conn.execute(text("CREATE TABLE reports (id VARCHAR(40) PRIMARY KEY, category VARCHAR(32), lat FLOAT, lng FLOAT, status VARCHAR(16))"))
        conn.execute(text("INSERT INTO reports VALUES ('keep-me', 'streetlight', 51.5, -0.09, 'verified')"))
    added = database.ensure_columns(old)
    assert {"reports.photo", "reports.has_photo", "reports.resolved_at", "reports.reporter_id"} <= set(added)
    assert {c["name"] for c in inspect(old).get_columns("reports")} >= {"photo", "has_photo", "resolved_at", "description", "updated_at"}
    assert database.ensure_columns(old) == []  # nothing left to add the second time
    with old.begin() as conn:
        assert conn.execute(text("SELECT id, status FROM reports")).all() == [("keep-me", "verified")]  # data untouched


# ---- the moderator listing ----------------------------------------------------------------------


def test_the_moderator_listing_includes_rejected_reports_and_admin_fields(client, make_report):
    a = make_report(status="unverified")
    b = make_report(status="verified")
    c = make_report(status="rejected")
    assert client.get("/api/admin/reports").status_code == 401  # moderators only
    everything = client.get("/api/admin/reports", headers=ADMIN).json()
    assert {r["id"] for r in everything} == {a.id, b.id, c.id}
    assert {"has_photo", "disputes", "reporter_id"} <= set(everything[0])
    only = client.get("/api/admin/reports", params={"status": "rejected"}, headers=ADMIN).json()
    assert [r["id"] for r in only] == [c.id]
    assert client.get("/api/admin/reports", params={"status": "bogus"}, headers=ADMIN).status_code == 422
    assert "rejected" not in {r["status"] for r in client.get("/api/reports").json()}  # the public list is unchanged


def test_the_moderator_listing_hides_simulated_reports_unless_asked(client, make_report):
    real = make_report(status="verified")
    sim = make_report(status="verified", simulated=True, source="simulated")
    ids = lambda **p: {r["id"] for r in client.get("/api/admin/reports", params=p, headers=ADMIN).json()}  # noqa: E731
    assert ids() == {real.id} and ids(include_simulated="true") == {real.id, sim.id}


def test_the_methodology_publishes_the_category_labels_the_interface_uses(client):
    cats = client.get("/api/methodology").json()["categories"]
    assert set(cats) == {"streetlight", "poor_lighting", "obstruction", "footpath", "visibility"}
    assert cats["streetlight"] == {"label": "Broken streetlight", "factor": "lighting", "severity": 1.0}
