"""Shared dependencies: who is calling, admin access, and abuse limits.

Who is calling
--------------
* A signed-in person sends a Supabase access token (`Authorization: Bearer <jwt>`). The token is verified
  here: HS256 with the project's JWT secret, or ES256/RS256 against the project's public keys (JWKS).
* A moderator is a signed-in person whose token carries `app_metadata.role` of "moderator" or "admin".
  `app_metadata` can only be set by the server side of Supabase. `user_metadata` can be edited by the
  user and the top-level `role` claim is just the Postgres role ("authenticated"), so neither is trusted.
* Anonymous Supabase sign-ins are real tokens but not real accounts. They never count as "logged in".
* If neither JWT setting is configured the API stays in its prototype mode: reporting is open and rate
  limited per client, and admin endpoints use the shared `X-Admin-Token`. If `ADMIN_TOKEN` is also
  empty, admin endpoints answer 503: they are switched off, not open.

Login is required for reporting, confirming and disputing when `REQUIRE_LOGIN=1`, or when it is left on
"auto" and a JWT setting is present.
"""
from __future__ import annotations

import hashlib
import os
import secrets
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from functools import lru_cache
from typing import Callable, Optional

import jwt
from fastapi import Header, HTTPException, Request

_UNSET = object()


# --------------------------------------------------------------------------------------------------
# Client address and hash (anonymous callers)
# --------------------------------------------------------------------------------------------------


def client_key(request: Request) -> str:
    """The caller's address, used only in memory for rate limits.

    Behind a trusted proxy (set TRUST_PROXY=1) the proxy appends the real client address as the last
    X-Forwarded-For entry, so that entry is used. Earlier entries can be forged by the caller.
    """
    if os.getenv("TRUST_PROXY") == "1":
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            return fwd.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


_fallback_salt = secrets.token_hex(16)


def _salt() -> str:
    return os.getenv("PRIVACY_SALT") or _fallback_salt


def client_hash(request: Request) -> str:
    """Salted one-way hash of the caller, so one browser cannot confirm a report twice.
    The address itself is never stored."""
    return hashlib.sha256((_salt() + client_key(request)).encode()).hexdigest()[:16]


# --------------------------------------------------------------------------------------------------
# Sign-in tokens
# --------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Identity:
    user_id: str
    role: str  # "user" | "moderator" | "admin"
    anonymous: bool = False

    @property
    def is_moderator(self) -> bool:
        return self.role in ("moderator", "admin")


def _jwks_url() -> str:
    explicit = os.getenv("SUPABASE_JWKS_URL", "").strip()
    if explicit:
        return explicit
    base = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
    return f"{base}/auth/v1/.well-known/jwks.json" if base else ""


def jwt_configured() -> bool:
    return bool(os.getenv("SUPABASE_JWT_SECRET", "").strip() or _jwks_url())


def login_required() -> bool:
    setting = os.getenv("REQUIRE_LOGIN", "auto").strip().lower()
    if setting in ("1", "true", "yes", "on"):
        return True
    if setting in ("0", "false", "no", "off"):
        return False
    return jwt_configured()


@lru_cache(maxsize=4)
def _jwks_client(url: str) -> "jwt.PyJWKClient":
    # Keys are cached in memory, so most requests do not call the identity provider.
    return jwt.PyJWKClient(url, cache_jwk_set=True, lifespan=600, timeout=5)


def _verify(token: str) -> dict:
    audience = os.getenv("SUPABASE_JWT_AUDIENCE", "authenticated")
    options = {"require": ["exp", "sub"]}
    try:
        alg = jwt.get_unverified_header(token).get("alg")
        if alg == "HS256" and os.getenv("SUPABASE_JWT_SECRET", "").strip():
            return jwt.decode(token, os.environ["SUPABASE_JWT_SECRET"].strip(), algorithms=["HS256"], audience=audience, options=options)
        if alg in ("ES256", "RS256") and _jwks_url():
            key = _jwks_client(_jwks_url()).get_signing_key_from_jwt(token).key
            return jwt.decode(token, key, algorithms=[alg], audience=audience, options=options)
    except jwt.ExpiredSignatureError as err:
        raise HTTPException(401, "Your sign-in has expired. Please sign in again.", headers={"WWW-Authenticate": "Bearer"}) from err
    except (jwt.InvalidTokenError, jwt.PyJWKClientError) as err:
        raise HTTPException(401, "Invalid sign-in token.", headers={"WWW-Authenticate": "Bearer"}) from err
    # Either sign-in is not configured here, or the token uses a method this server does not accept.
    raise HTTPException(401, "Sign-in is not configured for this kind of token on this server.", headers={"WWW-Authenticate": "Bearer"})


def _identity_from(claims: dict) -> Identity:
    app_meta = claims.get("app_metadata")
    role = app_meta.get("role") if isinstance(app_meta, dict) else None
    return Identity(
        user_id=str(claims["sub"]),
        role=role if role in ("moderator", "admin") else "user",
        anonymous=bool(claims.get("is_anonymous")),
    )


def optional_identity(request: Request) -> Optional[Identity]:
    """The signed-in person, or None if no token was sent. A token that is sent but invalid answers 401."""
    cached = getattr(request.state, "identity", _UNSET)
    if cached is not _UNSET:
        return cached  # type: ignore[return-value]
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    ident = _identity_from(_verify(token.strip())) if scheme.lower() == "bearer" and token.strip() else None
    request.state.identity = ident
    return ident


@dataclass(frozen=True)
class Caller:
    reporter_id: str  # stored on reports: "user:<id>" or "anon:<hash>"
    key: str  # 16 characters, stored on confirmations and disputes
    actor: str  # written to the audit trail
    account: bool


def caller(request: Request) -> Caller:
    """Who is making this public write? Raises 401 when login is required and nobody is signed in."""
    ident = optional_identity(request)
    if ident is not None and not ident.anonymous:
        digest = hashlib.sha256((_salt() + "user:" + ident.user_id).encode()).hexdigest()[:16]
        return Caller(f"user:{ident.user_id}", digest, f"user:{ident.user_id[:8]}", True)
    if login_required():
        raise HTTPException(401, "Sign in to do this.", headers={"WWW-Authenticate": "Bearer"})
    h = client_hash(request)
    return Caller(f"anon:{h}", h, "public", False)


# --------------------------------------------------------------------------------------------------
# Admin
# --------------------------------------------------------------------------------------------------


def require_admin(request: Request, x_admin_token: Optional[str] = Header(default=None)) -> str:
    """Return the name to put in the audit trail: "mod:<id>" for a signed-in moderator, "admin" for the shared token."""
    ident = optional_identity(request)
    if ident is not None:
        if ident.is_moderator and not ident.anonymous:
            return f"mod:{ident.user_id[:8]}"
        raise HTTPException(403, "This account is not a moderator.")
    expected = os.getenv("ADMIN_TOKEN", "")
    if not expected:
        if jwt_configured():
            raise HTTPException(401, "Sign in as a moderator.", headers={"WWW-Authenticate": "Bearer"})
        raise HTTPException(503, "Admin endpoints are switched off. Set ADMIN_TOKEN, or configure sign-in, on the server to enable them.")
    if not x_admin_token or not secrets.compare_digest(x_admin_token.encode(), expected.encode()):
        raise HTTPException(401, "Missing or invalid admin token.")
    return "admin"


# --------------------------------------------------------------------------------------------------
# Rate limits
# --------------------------------------------------------------------------------------------------


class RateLimiter:
    """Sliding-window limit per account (or per client address when nobody is signed in), held in memory
    (so per server process, and reset on restart)."""

    def __init__(self, env_var: str, default: int, window_s: float = 3600.0):
        self.env_var = env_var
        self.default = default
        self.window_s = window_s
        self._hits: dict[str, deque] = defaultdict(deque)

    @property
    def limit(self) -> int:
        try:
            return int(os.getenv(self.env_var, str(self.default)))
        except ValueError:
            return self.default

    def check(self, key: str) -> None:
        now = time.monotonic()
        q = self._hits[key]
        while q and now - q[0] > self.window_s:
            q.popleft()
        if len(q) >= self.limit:
            retry = max(1, int(self.window_s - (now - q[0])))
            raise HTTPException(429, "Too many requests. Please try again later.", headers={"Retry-After": str(retry)})
        q.append(now)
        if len(self._hits) > 10_000:  # drop idle clients so memory stays bounded
            for k in [k for k, v in self._hits.items() if not v]:
                del self._hits[k]

    def reset(self) -> None:
        self._hits.clear()


report_limiter = RateLimiter("REPORT_LIMIT_PER_HOUR", 10)
confirm_limiter = RateLimiter("CONFIRM_LIMIT_PER_HOUR", 30)
dispute_limiter = RateLimiter("DISPUTE_LIMIT_PER_HOUR", 10)
compare_limiter = RateLimiter("COMPARE_LIMIT_PER_HOUR", 60)
classify_limiter = RateLimiter("CLASSIFY_LIMIT_PER_HOUR", 300)
ALL_LIMITERS = (report_limiter, confirm_limiter, dispute_limiter, compare_limiter, classify_limiter)


def limited(limiter: RateLimiter, *, per_account: bool = False) -> Callable[[Request], None]:
    """Rate-limit dependency. With `per_account`, a signed-in person has their own budget, so people sharing one
    address (a campus, a mobile network) do not use each other's limits up, and one person cannot get a fresh
    budget by changing address. Public read-style endpoints ignore tokens entirely and limit by address."""

    def dependency(request: Request) -> None:
        ident = optional_identity(request) if per_account else None
        key = f"user:{ident.user_id}" if ident is not None and not ident.anonymous else client_key(request)
        limiter.check(key)

    return dependency


report_limit = limited(report_limiter, per_account=True)
confirm_limit = limited(confirm_limiter, per_account=True)
dispute_limit = limited(dispute_limiter, per_account=True)
compare_limit = limited(compare_limiter)
classify_limit = limited(classify_limiter)
