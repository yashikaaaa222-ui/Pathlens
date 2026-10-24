"""PathLens API.

Run from this folder:   uvicorn main:app --reload
Interactive docs:       http://localhost:8000/docs
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()

from ai.classify_report import classifier_info, get_classifier  # noqa: E402
from database import SessionLocal, init_db  # noqa: E402
from routes import dashboard_api, report_api, route_api  # noqa: E402
from routes.deps import jwt_configured, login_required  # noqa: E402
from services import retention  # noqa: E402

log = logging.getLogger("pathlens")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    try:
        get_classifier()  # train at start-up so the first report is not slow
    except Exception:
        log.exception("Classifier failed to start; reports will go to manual review")
    try:
        with SessionLocal() as db:
            removed = retention.purge(db)
        if any(removed.values()):
            log.info("Retention: %s", removed)
    except Exception:
        log.exception("Retention clean-up failed; it will run again at the next start-up")
    if not jwt_configured():
        log.warning("Sign-in is not configured (SUPABASE_JWT_SECRET / SUPABASE_URL). Reporting is open and admin uses the shared token.")
    elif not login_required():
        log.warning("Sign-in is configured but REQUIRE_LOGIN is off, so anonymous reporting is still allowed.")
    yield


app = FastAPI(
    title="PathLens API",
    version="0.2.0",
    description=(
        "Compare walking routes by reported street conditions, with the evidence, freshness and confidence behind "
        "every result. It does not predict crime and does not say that any route is safe."
    ),
    lifespan=lifespan,
)

origins = [o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=False,  # tokens travel in headers, never in cookies
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "X-Admin-Token", "Authorization"],
)

app.include_router(route_api.router)
app.include_router(report_api.router)
app.include_router(dashboard_api.router)


@app.get("/api/health", tags=["meta"])
def health() -> dict:
    """Cheap liveness check. Point a free uptime pinger at it every ~10 minutes to keep a free-tier host awake."""
    return {"status": "ok", "classifier_ready": classifier_info() is not None}
