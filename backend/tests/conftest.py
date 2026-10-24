import os

# Must be set before any application module is imported: the engine is created at import time.
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["ADMIN_TOKEN"] = "test-token"
for var in (
    "ORS_API_KEY", "TRUST_PROXY", "PRIVACY_SALT", "SUPABASE_JWT_SECRET", "SUPABASE_URL", "SUPABASE_JWKS_URL",
    "SUPABASE_JWT_AUDIENCE", "REQUIRE_LOGIN", "PHOTO_MAX_BYTES", "RETENTION_REJECTED_DAYS", "RETENTION_UNVERIFIED_DAYS",
    "RETENTION_PHOTO_DAYS",
):
    os.environ.pop(var, None)

from datetime import datetime, timezone  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import database  # noqa: E402
import models  # noqa: E402,F401
from models.report import Report, ReportEvent  # noqa: E402
from routes.deps import ALL_LIMITERS  # noqa: E402
from services import routing  # noqa: E402
from services.risk_engine import RouteGeom  # noqa: E402

ADMIN = {"X-Admin-Token": "test-token"}


@pytest.fixture(scope="session")
def client():
    import main

    with TestClient(main.app) as c:  # runs start-up once: tables and classifier
        yield c


@pytest.fixture(autouse=True)
def clean_state():
    database.Base.metadata.drop_all(database.engine)
    database.Base.metadata.create_all(database.engine)
    for limiter in ALL_LIMITERS:
        limiter.reset()
    routing.clear_cache()
    yield


@pytest.fixture
def db():
    session = database.SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def make_report(db):
    """Insert a report straight into the database, with full control over time and status."""

    def _make(**kw) -> Report:
        now = datetime.now(timezone.utc)
        row = dict(
            category="streetlight", lat=51.5, lng=-0.0925, description="", status="unverified",
            source="community", simulated=False, reported_at=now, verified_at=None,
        )
        row.update(kw)
        if row["status"] == "verified" and row["verified_at"] is None:
            row["verified_at"] = row["reported_at"]
        report = Report(**row)
        db.add(report)
        db.flush()
        db.add(ReportEvent(report_id=report.id, action="created", actor="public"))
        db.commit()
        return report

    return _make


# Two neutral test routes, about 1 km long, between the same two points.
def _line(lat: float, lng0: float, lng1: float, n: int = 8) -> list[tuple[float, float]]:
    return [(lat, lng0 + (lng1 - lng0) * i / n) for i in range(n + 1)]


ROUTE_A_COORDS = _line(51.5000, -0.1000, -0.0850)
ROUTE_B_COORDS = [(51.5, -0.1)] + _line(51.5020, -0.0985, -0.0865) + [(51.5, -0.085)]
MID_A = (51.5000, -0.0925)  # the middle of route A

COMPARE_BODY = {
    "origin": {"lat": 51.5, "lng": -0.1},
    "destination": {"lat": 51.5, "lng": -0.085},
    "at": "2026-12-15T22:00:00Z",  # a winter night: lighting counts in full
}


@pytest.fixture
def fake_routes(monkeypatch):
    async def fake(origin, dest, *, client=None):
        return [RouteGeom(list(ROUTE_A_COORDS), 1040.0, 750.0, "test"), RouteGeom(list(ROUTE_B_COORDS), 1120.0, 820.0, "test")]

    monkeypatch.setattr(routing, "get_routes", fake)
