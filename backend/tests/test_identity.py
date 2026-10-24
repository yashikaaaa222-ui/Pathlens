"""Sign-in: who is calling, who is a moderator, and what each account may do."""
import base64
import hashlib
import hmac
import json
import time
import uuid
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy import select

from models.report import Report, ReportEvent
from routes import deps

from conftest import ADMIN, COMPARE_BODY, MID_A

SECRET = "unit-test-secret-which-is-long-enough-32b!"
REPORT = {"category": "streetlight", "lat": MID_A[0], "lng": MID_A[1], "description": "Light is out near the corner", "consent": True}


def token(sub=None, *, secret=SECRET, alg="HS256", exp=3600, headers=None, **claims):
    payload = {"sub": sub or str(uuid.uuid4()), "aud": "authenticated", "exp": int(time.time()) + exp, "role": "authenticated", **claims}
    return jwt.encode(payload, secret, algorithm=alg, headers=headers)


def bearer(tok):
    return {"Authorization": f"Bearer {tok}"}


@pytest.fixture
def login(monkeypatch):
    """Switch sign-in on, the way a Supabase deployment would."""
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SECRET)


# ---- login for reporters -------------------------------------------------------------------------


def test_without_sign_in_configured_reporting_stays_open_and_tokens_are_not_trusted(client):
    assert client.post("/api/reports", json=REPORT).status_code == 201  # prototype mode
    r = client.post("/api/reports", json=REPORT, headers=bearer(token()))
    assert r.status_code == 401 and "not configured" in r.json()["detail"]


def test_login_is_required_for_every_public_write_once_sign_in_is_configured(client, login, make_report):
    existing = make_report(status="verified")
    assert client.post("/api/reports", json=REPORT).status_code == 401
    assert client.post(f"/api/reports/{existing.id}/confirmations", json={"still_present": True}).status_code == 401
    assert client.post(f"/api/reports/{existing.id}/disputes", json={"reason": "This was fixed already"}).status_code == 401
    assert "Sign in" in client.post("/api/reports", json=REPORT).json()["detail"]


def test_reads_stay_public_when_login_is_required(client, login, make_report):
    make_report(status="verified")
    assert client.get("/api/reports").status_code == 200
    assert client.get("/api/methodology").status_code == 200
    assert client.post("/api/reports/classify", json={"text": "street light is broken"}).status_code == 200


def test_a_signed_in_report_belongs_to_the_account(client, login, db):
    sub = str(uuid.uuid4())
    r = client.post("/api/reports", json=REPORT, headers=bearer(token(sub)))
    assert r.status_code == 201
    row = db.get(Report, r.json()["id"])
    assert row.reporter_id == f"user:{sub}"
    events = client.get(f"/api/reports/{row.id}/events", headers=ADMIN).json()
    assert events[0]["actor"] == f"user:{sub[:8]}"  # a short pseudonym, never the email


def test_an_anonymous_sign_in_does_not_count_as_a_login(client, login):
    r = client.post("/api/reports", json=REPORT, headers=bearer(token(is_anonymous=True)))
    assert r.status_code == 401


def test_login_can_be_switched_off_explicitly(client, login, monkeypatch):
    monkeypatch.setenv("REQUIRE_LOGIN", "0")
    assert client.post("/api/reports", json=REPORT).status_code == 201


@pytest.mark.parametrize(
    "make",
    [
        pytest.param(lambda: token(exp=-30), id="expired"),
        pytest.param(lambda: token(secret="a-different-secret-that-is-also-32-bytes-long"), id="wrong-secret"),
        pytest.param(lambda: token(aud="somewhere-else"), id="wrong-audience"),
        pytest.param(lambda: jwt.encode({"sub": "x", "aud": "authenticated", "exp": int(time.time()) + 60}, None, algorithm="none"), id="alg-none"),
        pytest.param(lambda: jwt.encode({"aud": "authenticated", "exp": int(time.time()) + 60}, SECRET, algorithm="HS256"), id="no-subject"),
        pytest.param(lambda: jwt.encode({"sub": "x", "aud": "authenticated"}, SECRET, algorithm="HS256"), id="no-expiry"),
        pytest.param(lambda: "not.a.token", id="garbage"),
    ],
)
def test_bad_tokens_are_refused(client, login, make):
    assert client.post("/api/reports", json=REPORT, headers=bearer(make())).status_code == 401


def test_public_read_endpoints_ignore_tokens_entirely(client, fake_routes):
    # An expired token must not break a route comparison, which needs no account.
    r = client.post("/api/routes/compare", json=COMPARE_BODY, headers=bearer("garbage"))
    assert r.status_code == 200


# ---- moderators ----------------------------------------------------------------------------------


def test_a_moderator_account_can_moderate_and_is_named_in_the_audit_trail(client, login, make_report):
    r = make_report()
    sub = str(uuid.uuid4())
    ok = client.patch(f"/api/reports/{r.id}/status", json={"status": "verified", "note": "Checked on site"}, headers=bearer(token(sub, app_metadata={"role": "moderator"})))
    assert ok.status_code == 200 and ok.json()["status"] == "verified"
    trail = client.get(f"/api/reports/{r.id}/events", headers=ADMIN).json()
    assert trail[-1]["actor"] == f"mod:{sub[:8]}" and trail[-1]["note"] == "Checked on site"


def test_the_admin_role_in_app_metadata_also_works(client, login, make_report):
    r = make_report()
    ok = client.patch(f"/api/reports/{r.id}/status", json={"status": "verified"}, headers=bearer(token(app_metadata={"role": "admin"})))
    assert ok.status_code == 200


@pytest.mark.parametrize(
    "claims",
    [
        pytest.param({}, id="plain-user"),
        pytest.param({"user_metadata": {"role": "admin"}}, id="user-editable-metadata"),
        pytest.param({"role": "admin"}, id="top-level-role-claim"),
        pytest.param({"app_metadata": {"role": "moderator"}, "is_anonymous": True}, id="anonymous-moderator"),
    ],
)
def test_only_app_metadata_makes_a_moderator(client, login, make_report, claims):
    r = make_report()
    resp = client.patch(f"/api/reports/{r.id}/status", json={"status": "verified"}, headers=bearer(token(**claims)))
    assert resp.status_code == 403


def test_admin_endpoints_need_a_moderator_sign_in_when_no_shared_token_is_set(client, login, monkeypatch):
    monkeypatch.delenv("ADMIN_TOKEN")
    assert client.get("/api/dashboard/stats").status_code == 401  # sign-in is configured, so "sign in", not "switched off"


def test_the_shared_admin_token_still_works_beside_sign_in(client, login, make_report, db):
    r = make_report()
    assert client.patch(f"/api/reports/{r.id}/status", json={"status": "verified"}, headers=ADMIN).status_code == 200
    assert db.scalars(select(ReportEvent).where(ReportEvent.report_id == r.id).order_by(ReportEvent.id)).all()[-1].actor == "admin"


# ---- per-account limits and independence ---------------------------------------------------------


def test_each_account_has_its_own_rate_limit(client, login, monkeypatch):
    monkeypatch.setenv("REPORT_LIMIT_PER_HOUR", "2")
    a, b = bearer(token()), bearer(token())
    assert [client.post("/api/reports", json=REPORT, headers=a).status_code for _ in range(3)] == [201, 201, 429]
    assert client.post("/api/reports", json=REPORT, headers=b).status_code == 201  # same address, separate budget


def test_you_cannot_confirm_or_dispute_your_own_report_but_someone_else_can(client, login):
    a, b = bearer(token()), bearer(token())
    rid = client.post("/api/reports", json=REPORT, headers=a).json()["id"]
    assert client.post(f"/api/reports/{rid}/confirmations", json={"still_present": True}, headers=a).status_code == 403
    assert client.post(f"/api/reports/{rid}/disputes", json={"reason": "This is not right"}, headers=a).status_code == 403
    assert client.post(f"/api/reports/{rid}/confirmations", json={"still_present": True}, headers=b).status_code == 201
    assert client.post(f"/api/reports/{rid}/disputes", json={"reason": "This is not right"}, headers=b).status_code == 201


def test_two_accounts_corroborate_each_other_but_one_account_cannot_corroborate_itself(client, login, fake_routes):
    def counted():
        return client.post("/api/routes/compare", json=COMPARE_BODY).json()["routes"][0]["metrics"]["counted"]

    a, b = bearer(token()), bearer(token())
    client.post("/api/reports", json=REPORT, headers=a)
    client.post("/api/reports", json={**REPORT, "lat": MID_A[0] + 0.00002}, headers=a)
    assert counted() == 0  # the same account twice is still one voice
    client.post("/api/reports", json={**REPORT, "lat": MID_A[0] + 0.00004}, headers=b)
    assert counted() == 3  # a second independent account corroborates all three (at half weight each)


# ---- asymmetric keys (newer Supabase projects) -----------------------------------------------------


@pytest.fixture
def jwks(monkeypatch):
    """Pretend the identity provider publishes an EC key, without calling the network."""
    key = ec.generate_private_key(ec.SECP256R1())
    seen = {}

    class FakeClient:
        def get_signing_key_from_jwt(self, tok):
            return SimpleNamespace(key=key.public_key())

    def fake_client(url):
        seen["url"] = url
        return FakeClient()

    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co/")
    monkeypatch.setattr(deps, "_jwks_client", fake_client)
    return SimpleNamespace(key=key, seen=seen)


def test_tokens_signed_with_the_projects_public_key_are_accepted(client, jwks):
    tok = token(secret=jwks.key, alg="ES256", headers={"kid": "k1"})
    assert client.post("/api/reports", json=REPORT, headers=bearer(tok)).status_code == 201
    assert jwks.seen["url"] == "https://example.supabase.co/auth/v1/.well-known/jwks.json"


def test_tokens_signed_with_some_other_key_are_refused(client, jwks):
    other = ec.generate_private_key(ec.SECP256R1())
    tok = token(secret=other, alg="ES256", headers={"kid": "k1"})
    assert client.post("/api/reports", json=REPORT, headers=bearer(tok)).status_code == 401


def test_a_symmetric_token_cannot_be_signed_with_the_public_key(client, jwks):
    # The classic algorithm-confusion attack: sign HS256 using the published public key as the secret.
    # PyJWT's own encoder refuses to do that, so the attacker's token is built by hand with a raw HMAC.
    public_pem = jwks.key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)

    def b64(raw: bytes) -> bytes:
        return base64.urlsafe_b64encode(raw).rstrip(b"=")

    claims = {"sub": "attacker", "aud": "authenticated", "exp": int(time.time()) + 60, "app_metadata": {"role": "admin"}}
    signing_input = b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode()) + b"." + b64(json.dumps(claims).encode())
    forged = (signing_input + b"." + b64(hmac.new(public_pem, signing_input, hashlib.sha256).digest())).decode()
    assert client.post("/api/reports", json=REPORT, headers=bearer(forged)).status_code == 401
    # ...and the forged moderator token opens nothing either.
    assert client.get("/api/dashboard/stats", headers=bearer(forged)).status_code == 401
