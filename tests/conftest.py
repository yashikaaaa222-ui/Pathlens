import os

# Must be set before any application module is imported: the database engine is created at import time.
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["ADMIN_TOKEN"] = "test-token"
for var in ("SUPABASE_JWT_SECRET", "SUPABASE_URL", "SUPABASE_JWKS_URL", "REQUIRE_LOGIN", "TRUST_PROXY", "PRIVACY_SALT"):
    os.environ.pop(var, None)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import database  # noqa: E402
import models  # noqa: E402,F401
from routes.deps import ALL_LIMITERS  # noqa: E402

ADMIN = {"X-Admin-Token": "test-token"}


@pytest.fixture(scope="session")
def client():
    import main

    with TestClient(main.app) as c:
        yield c


@pytest.fixture(autouse=True)
def clean_state():
    database.Base.metadata.drop_all(database.engine)
    database.Base.metadata.create_all(database.engine)
    for limiter in ALL_LIMITERS:
        limiter.reset()
    yield


@pytest.fixture
def db_session():
    session = database.SessionLocal()
    try:
        yield session
    finally:
        session.close()
