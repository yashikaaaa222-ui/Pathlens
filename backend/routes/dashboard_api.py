"""Moderator dashboard, public methodology, and data operations (OSM baseline, demo data)."""
from __future__ import annotations

import csv
import io
import statistics
from datetime import timedelta
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ai.classify_report import classifier_info
from ai.duplicate_detection import WINDOW_DAYS, RADIUS_M, Candidate, find_matches
from database import get_db, utcnow
from models.infra import InfraFeature
from models.report import Report, ReportConfirmation, ReportDispute, ReportEvent
from routes.deps import login_required, require_admin
from routes.report_api import confirmation_counts, dispute_counts, to_admin
from schemas.dashboard import BaselineBox, BaselineLoad, DemoSeedRequest, QueueItem, StatsOut
from schemas.report import ConfirmationCounts, ReportAdmin
from services import daylight, freshness, osm_baseline, retention, routing
from services.demo_data import simulate_for_routes
from services.osm_baseline import BaselineError
from services.risk_engine import BAND_TEXT, CATEGORIES, DEFAULT_WEIGHTS, PERSONAS, Config
from services.routing import RoutingError

router = APIRouter(prefix="/api", tags=["dashboard"])


def _real():
    return Report.simulated.is_(False)


# --------------------------------------------------------------------------------------------------
# Admin: statistics and moderation queue
# --------------------------------------------------------------------------------------------------


@router.get("/dashboard/stats", response_model=StatsOut)
def stats(db: Session = Depends(get_db), _: str = Depends(require_admin)) -> StatsOut:
    """Data quality at a glance. Simulated demo reports are counted separately and kept out of every other figure."""
    now = utcnow()
    real = db.scalars(select(Report).where(_real())).all()

    def tally(key) -> dict[str, int]:
        out: dict[str, int] = {}
        for r in real:
            out[key(r)] = out.get(key(r), 0) + 1
        return out

    pending = [r for r in real if r.status == "unverified"]
    pending_ages = [(freshness.as_utc(now) - freshness.as_utc(r.reported_at)).total_seconds() / 86_400 for r in pending]
    verified_ages = [freshness.age_days(r, now) for r in real if r.status == "verified"]

    conf = {"still_present": 0, "fixed": 0}
    for still, n in db.execute(
        select(ReportConfirmation.still_present, func.count()).join(Report, Report.id == ReportConfirmation.report_id).where(_real()).group_by(ReportConfirmation.still_present)
    ):
        conf["still_present" if still else "fixed"] = n

    judged = [r for r in real if r.classifier_category]
    agree = sum(1 for r in judged if r.classifier_category == r.category)
    classifier = dict(classifier_info() or {"available": False})
    classifier["agreement_with_final_category"] = round(agree / len(judged), 3) if judged else None
    classifier["agreement_evaluated"] = len(judged)

    kinds = dict(db.execute(select(InfraFeature.kind, func.count()).group_by(InfraFeature.kind)).all())
    last = db.scalar(select(func.max(InfraFeature.imported_at)))
    baseline = {
        "features_by_kind": kinds,
        "total": sum(kinds.values()),
        "last_imported": freshness.as_utc(last).isoformat() if last else None,
    }

    days_to_resolve = [
        (freshness.as_utc(r.resolved_at) - freshness.as_utc(r.verified_at or r.reported_at)).total_seconds() / 86_400
        for r in real
        if r.status == "resolved" and r.resolved_at
    ]
    disputed = db.scalar(
        select(func.count(func.distinct(ReportDispute.report_id))).join(Report, Report.id == ReportDispute.report_id).where(_real(), Report.status != "rejected")
    ) or 0

    over_7 = sum(1 for a in pending_ages if a > 7)
    warnings: list[str] = []
    if disputed:
        warnings.append(f"{disputed} report{'s are' if disputed != 1 else ' is'} disputed. Check the queue: a disputed verified report still counts toward scores until a moderator decides.")
    if not baseline["total"]:
        warnings.append("No OpenStreetMap baseline is loaded, so the help-point, streetlight and activity layers are unavailable.")
    if over_7:
        warnings.append(f"{over_7} pending report{'s have' if over_7 != 1 else ' has'} waited more than 7 days.")
    if not any(r.status == "verified" for r in real):
        warnings.append("There are no verified reports yet, so route comparisons will mostly show 'not enough data'.")
    if not classifier_info():
        warnings.append("The report classifier is unavailable. Reports are still saved and go to manual review.")

    return StatsOut(
        total=len(real),
        by_status=tally(lambda r: r.status),
        by_category=tally(lambda r: r.category),
        by_source=tally(lambda r: r.source),
        simulated=db.scalar(select(func.count()).select_from(Report).where(Report.simulated.is_(True))) or 0,
        pending=len(pending),
        pending_over_7_days=over_7,
        oldest_pending_days=round(max(pending_ages), 1) if pending_ages else None,
        median_verified_age_days=round(statistics.median(verified_ages), 1) if verified_ages else None,
        median_days_to_resolve=round(statistics.median(days_to_resolve), 1) if days_to_resolve else None,
        disputed=disputed,
        with_photo=sum(1 for r in real if r.has_photo or r.photo_url),
        possible_duplicates=sum(1 for r in real if r.possible_duplicate_of and r.status != "rejected"),
        confirmations=conf,
        classifier=classifier,
        baseline=baseline,
        warnings=warnings,
    )


@router.get("/dashboard/queue", response_model=list[QueueItem])
def queue(limit: int = Query(50, ge=1, le=200), db: Session = Depends(get_db), _: str = Depends(require_admin)) -> list[QueueItem]:
    """What moderators should look at, most worth reviewing first.

    Pending reports: priority adds up from how severe the category is, how many similar reports sit nearby
    (so one review can settle a cluster), how many people tapped "still there", disputes, a photo, and how
    long it has waited.

    Flagged reports: verified reports that people dispute, or that two or more people say are fixed. They
    already count toward scores, so they come first.
    """
    now = utcnow()
    pending = db.scalars(select(Report).where(Report.status == "unverified", _real())).all()
    verified = db.scalars(select(Report).where(Report.status == "verified", _real())).all()
    recent = db.scalars(
        select(Report).where(Report.status != "rejected", _real(), Report.reported_at >= now - timedelta(days=WINDOW_DAYS + 1))
    ).all()
    pool = [Candidate(r.id, r.category, r.lat, r.lng, r.reported_at, r.description or "") for r in recent]
    ids = [r.id for r in (*pending, *verified)]
    counts = confirmation_counts(db, ids)
    disputes = dispute_counts(db, ids)

    def plural(n: int, word: str) -> str:
        return f"{n} {word}{'' if n == 1 else 's'}"

    items: list[QueueItem] = []
    for r in pending:
        matches = find_matches(Candidate(r.id, r.category, r.lat, r.lng, r.reported_at, r.description or ""), pool)
        c = counts.get(r.id, ConfirmationCounts())
        d = disputes.get(r.id, 0)
        age = (freshness.as_utc(now) - freshness.as_utc(r.reported_at)).total_seconds() / 86_400
        sev = CATEGORIES[r.category].severity
        parts = [
            (sev, f"{CATEGORIES[r.category].label} (severity {sev:g})"),
            (min(len(matches), 3) * 0.5, f"{plural(len(matches), 'similar report')} within {RADIUS_M:.0f} m") if matches else (0.0, ""),
            (min(c.still_present, 3) * 0.25, f"{plural(c.still_present, 'confirmation')} that it is still there") if c.still_present else (0.0, ""),
            (min(d, 3) * 0.5, f"{plural(d, 'dispute')} against it") if d else (0.0, ""),
            (0.25, "has a photo") if (r.has_photo or r.photo_url) else (0.0, ""),
            (min(age, 14) / 14 * 0.5, f"waiting {age:.0f} day{'s' if round(age) != 1 else ''}") if age >= 1 else (0.0, ""),
        ]
        items.append(
            QueueItem(
                report=to_admin(r, counts, now, disputes),
                priority=round(sum(p for p, _ in parts), 2),
                reasons=[t for p, t in parts if t],
                similar_nearby=len(matches),
                kind="pending",
            )
        )

    for r in verified:
        c = counts.get(r.id, ConfirmationCounts())
        d = disputes.get(r.id, 0)
        if d < 1 and c.fixed < 2:
            continue
        parts = [
            (2.0, "verified, so it counts toward scores while this is open"),
            (min(d, 3) * 0.5, f"{plural(d, 'dispute')} against it") if d else (0.0, ""),
            (min(c.fixed, 3) * 0.5, f"{plural(c.fixed, 'person')} say it is fixed") if c.fixed >= 2 else (0.0, ""),
        ]
        items.append(
            QueueItem(
                report=to_admin(r, counts, now, disputes),
                priority=round(sum(p for p, _ in parts), 2),
                reasons=[t for p, t in parts if t],
                similar_nearby=0,
                kind="flagged",
            )
        )
    items.sort(key=lambda i: (-i.priority, i.report.reported_at))
    return items[:limit]


@router.get("/admin/reports", response_model=list[ReportAdmin])
def admin_reports(
    status: Optional[Literal["unverified", "verified", "resolved", "rejected"]] = None,
    include_simulated: bool = False,
    limit: int = Query(200, ge=1, le=500),
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
) -> list[ReportAdmin]:
    """Moderator listing by status, newest first. Unlike the public list it includes rejected reports,
    free text of unverified ones, photo and dispute flags."""
    q = select(Report)
    if status:
        q = q.where(Report.status == status)
    if not include_simulated:
        q = q.where(_real())
    rows = db.scalars(q.order_by(Report.reported_at.desc()).limit(limit)).all()
    ids = [r.id for r in rows]
    counts, disputes, now = confirmation_counts(db, ids), dispute_counts(db, ids), utcnow()
    return [to_admin(r, counts, now, disputes) for r in rows]


# --------------------------------------------------------------------------------------------------
# Public: methodology
# --------------------------------------------------------------------------------------------------


@router.get("/methodology")
def methodology() -> dict:
    """Everything a person needs to judge the score: weights, parameters, limits, privacy, data layers."""
    cfg = Config()
    return {
        "product": "PathLens",
        "purpose": "Compare walking routes by reported street conditions, and show how much evidence is behind each result.",
        "does_not": [
            "Predict crime or say that any route is safe.",
            "Treat missing information as proof that nothing is wrong.",
            "Score neighbourhoods. Only the routes you ask about are scored.",
            "Treat unverified reports as facts. They are labelled, and a single unverified report is shown but not counted.",
        ],
        "categories": {k: {"label": c.label, "factor": c.factor, "severity": c.severity} for k, c in CATEGORIES.items()},
        "score": {
            "meaning": "Reported concern from 0 to 100, built from counted reports per kilometre. It is a comparison aid, not a safety rating.",
            "scored_factors": {"lighting": "Broken streetlights and poorly lit stretches", "hazard": "Obstructions, damaged footpaths, poor visibility"},
            "default_weights": DEFAULT_WEIGHTS,
            "weights_status": "Provisional and unvalidated. They are published here, can be changed per request, and a sensitivity check shows how often the best route changes when they move.",
            "parameters": {
                "chunk_m": cfg.chunk_m, "buffer_m": cfg.buffer_m, "steepness": cfg.steepness, "smoothing_km": cfg.smoothing_km,
                "insufficient_below_coverage": cfg.insufficient_below,
            },
            "bands": {"insufficient": BAND_TEXT["insufficient"], f"low (score under {cfg.band_low_below})": BAND_TEXT["low"],
                      f"moderate (under {cfg.band_moderate_below})": BAND_TEXT["moderate"], "elevated": BAND_TEXT["elevated"]},
            "range": "Each score comes with a range: the score if the unobserved part of the route looked like the observed part.",
        },
        "personas": {k: {"label": p.label, "description": p.description, "weights": dict(p.weights), "severity_boost": dict(p.severity_boost)} for k, p in PERSONAS.items()},
        "time_of_day": {
            "rule": "Lighting counts in full after dark and for less in daylight.",
            "multipliers": daylight.LIGHTING_MULTIPLIER,
            "periods": "day: sun above the horizon; twilight: up to 6 degrees below; night: lower than that.",
        },
        "trust_tiers": [
            "Verified by a moderator: counts in full.",
            "Unverified, corroborated by two independent reporters in the same 100 m stretch: counts at half weight.",
            "Unverified, single report: shown on the map, not counted.",
            f"Unverified reports expire after {cfg.unverified_expiry_days:.0f} days. Resolved and rejected reports never count.",
        ],
        "freshness": {
            "half_life_days": freshness.HALF_LIFE_DAYS,
            "note": "A report's weight halves every half-life, which differs by category: a broken light lasts until fixed, an obstruction is usually gone in days.",
            "coverage_window_days": freshness.OBSERVE_DAYS,
        },
        "confidence": "Reported separately from concern. It measures how much of the route has recent evidence. Little evidence lowers confidence and never lowers concern.",
        "informational_layers": [
            "Help points (police, hospital, clinic, pharmacy, fire station), mapped streetlights and surrounding activity come from an OpenStreetMap snapshot.",
            "They are shown, not scored, because missing map data and under-served areas would otherwise be penalised for things the road did not cause.",
        ],
        "data_layers": [
            "OpenStreetMap baseline: uniform structure, no matter how many people report.",
            "Field audit: reports made and verified by the team walking a corridor, labelled as such.",
            "Community reports: moderated before they count.",
            "Simulated: demo data only, always flagged, hidden unless asked for, and removable in one call.",
        ],
        "fairness": [
            "Routes and individual reports are scored, never neighbourhoods. There are no area heatmaps.",
            "Reporting bias is real: engaged, well-connected areas file more reports. Few reports does not mean few problems, so the interface shows coverage next to every score.",
            "Wording says 'reported concern' and 'conditions', not 'danger' or 'unsafe'.",
        ],
        "privacy": [
            "Origin and destination are used to fetch routes and are not stored. They are kept briefly in an in-memory cache and never written to the database or a log.",
            "Reports store a location, a category and optional text. Phone numbers, email addresses, links, @handles and vehicle numbers in text are refused. Names cannot be detected automatically, so moderators read text before it is shown publicly.",
            "Sending a report needs your consent to this notice (the consent field).",
            "Reports, confirmations and disputes carry either your account id or, if you are not signed in, a salted one-way hash of the client address. It is used only to stop one person corroborating their own report or voting repeatedly. No IP address is stored, and the reporter is never shown publicly.",
            "Photos are rebuilt on the server, which removes camera details and GPS position. Only moderators can see them, because they can show faces or number plates, and they are deleted when a report is rejected. They are not blurred automatically.",
            "Routes come from OpenRouteService or a community OSRM server, and place names go to Nominatim, so those services see the queries.",
            *retention.RetentionPolicy.from_env().describe(),
        ],
        "accounts": {
            "login_required_to_report": login_required(),
            "moderators": "Moderators are signed-in accounts with the moderator role set by the server. Every status change is written to the audit trail with who made it, and a rejection needs a reason.",
        },
        "abuse_controls": [
            "Reporting, confirming and disputing are rate limited per account, or per client when nobody is signed in.",
            "A single unverified report is shown but not counted. Corroboration needs two independent reporters, and one person cannot corroborate themselves.",
            "Anyone signed in can dispute a report. A disputed verified report goes to the top of the moderators' queue; it keeps counting until a moderator decides.",
            "Unverified reports stop counting after 60 days.",
        ],
        "classifier": classifier_info(),
        "limits": [
            "Weights are provisional.",
            "OpenStreetMap coverage varies by city.",
            "The classifier is trained on a small synthetic set and only suggests.",
        ],
    }


# --------------------------------------------------------------------------------------------------
# Admin: retention and the civic export
# --------------------------------------------------------------------------------------------------


@router.post("/admin/maintenance")
def maintenance(db: Session = Depends(get_db), _: str = Depends(require_admin)) -> dict:
    """Apply the retention rules now (they also run when the server starts)."""
    return {**retention.purge(db), "policy": retention.RetentionPolicy.from_env().describe()}


def _csv_safe(value: str) -> str:
    """A spreadsheet runs text that starts with = + - or @ as a formula, so defuse it."""
    return "'" + value if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value


@router.get("/dashboard/export")
def export_issues(
    format: Literal["json", "csv"] = "json",
    status: Literal["verified", "resolved", "all"] = "all",
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
):
    """Verified and resolved issues for the people who can fix them (a city or ward office), with how long each
    has been open. Infrastructure only: no reporter, no photo, no classifier data."""
    now = utcnow()
    wanted = ("verified", "resolved") if status == "all" else (status,)
    rows = db.scalars(select(Report).where(_real(), Report.status.in_(wanted)).order_by(Report.verified_at)).all()
    out = []
    for r in rows:
        start = freshness.as_utc(r.verified_at or r.reported_at)
        end = freshness.as_utc(r.resolved_at) if r.resolved_at else freshness.as_utc(now)
        out.append(
            {
                "id": r.id,
                "category": r.category,
                "label": CATEGORIES[r.category].label,
                "status": r.status,
                "source": r.source,
                "lat": r.lat,
                "lng": r.lng,
                "description": r.description or "",
                "reported_at": freshness.as_utc(r.reported_at).isoformat(),
                "verified_at": freshness.as_utc(r.verified_at).isoformat() if r.verified_at else "",
                "resolved_at": freshness.as_utc(r.resolved_at).isoformat() if r.resolved_at else "",
                "days_open": round((end - start).total_seconds() / 86_400, 1),
            }
        )
    if format == "json":
        return {"count": len(out), "generated_at": now.isoformat(), "issues": out}
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(out[0].keys()) if out else ["id"])
    writer.writeheader()
    for row in out:
        writer.writerow({k: _csv_safe(v) if isinstance(v, str) else v for k, v in row.items()})
    return Response(buf.getvalue(), media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="pathlens-issues.csv"', "Cache-Control": "no-store"})


# --------------------------------------------------------------------------------------------------
# Admin: OpenStreetMap baseline
# --------------------------------------------------------------------------------------------------


def _store(db: Session, data: dict) -> dict:
    feats = osm_baseline.parse_overpass(data)
    result = osm_baseline.store_features(db, feats)
    by_kind: dict[str, int] = {}
    for f in feats:
        by_kind[f["kind"]] = by_kind.get(f["kind"], 0) + 1
    return {**result, "by_kind": by_kind}


@router.post("/admin/baseline/load")
def baseline_load(payload: BaselineLoad, db: Session = Depends(get_db), _: str = Depends(require_admin)) -> dict:
    """Load a saved Overpass JSON snapshot (an object with an `elements` list). Prefer this to live import:
    take the snapshot ahead of time so a demo never waits on Overpass."""
    return _store(db, {"elements": payload.elements})


@router.post("/admin/baseline/import")
async def baseline_import(payload: BaselineBox, db: Session = Depends(get_db), _: str = Depends(require_admin)) -> dict:
    """Fetch streetlights, help points and activity points for a bounding box from Overpass and store them."""
    try:
        data = await osm_baseline.fetch_overpass(payload.south, payload.west, payload.north, payload.east)
    except BaselineError as err:
        raise HTTPException(err.status, err.message) from err
    return _store(db, data)


# --------------------------------------------------------------------------------------------------
# Admin: simulated demo data
# --------------------------------------------------------------------------------------------------


@router.post("/admin/demo-seed", status_code=201)
async def demo_seed(payload: DemoSeedRequest, db: Session = Depends(get_db), _: str = Depends(require_admin)) -> dict:
    """Create SIMULATED reports around the routes between two points. Every row is flagged simulated.
    Safe to repeat: the same route produces the same ids, so rows are not duplicated."""
    try:
        routes = await routing.get_routes((payload.origin.lat, payload.origin.lng), (payload.destination.lat, payload.destination.lng))
    except RoutingError as err:
        raise HTTPException(err.status, err.message) from err
    created = skipped = 0
    for row in simulate_for_routes(routes, utcnow()):
        if db.get(Report, row["id"]) is not None:
            skipped += 1
            continue
        db.add(Report(**row))
        db.add(ReportEvent(report_id=row["id"], action="created", actor="system", note="simulated demo data"))
        created += 1
    db.commit()
    return {
        "created": created,
        "already_present": skipped,
        "note": "All of these are simulated. They are hidden from the public list and from comparisons unless include_simulated is set.",
    }


@router.delete("/admin/demo-seed")
def demo_clear(db: Session = Depends(get_db), _: str = Depends(require_admin)) -> dict:
    """Remove every simulated report, for a clean slate before showing real data."""
    rows = db.scalars(select(Report).where(Report.simulated.is_(True))).all()
    for r in rows:
        db.delete(r)  # events and confirmations go with it
    db.commit()
    return {"deleted": len(rows)}
