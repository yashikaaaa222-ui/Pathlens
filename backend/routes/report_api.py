"""Report intake, public listing, "still there" confirmations, and moderation."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ai.classify_report import classify_text
from ai.duplicate_detection import WINDOW_DAYS, Candidate, find_matches
from database import get_db, utcnow
from models.report import Report, ReportConfirmation, ReportDispute, ReportEvent, new_id
from routes.deps import Caller, caller, classify_limit, confirm_limit, dispute_limit, report_limit, require_admin
from schemas.report import (
    ClassifyRequest,
    ClassifyResponse,
    ConfirmationCounts,
    ConfirmationCreate,
    DisputeCounts,
    DisputeCreate,
    EventOut,
    FieldAuditCreate,
    ReportAdmin,
    ReportCreate,
    ReportCreated,
    ReportPublic,
    StatusChange,
    Suggestion,
)
from services.freshness import as_utc
from services.geo import M_PER_DEG_LAT, m_per_deg_lng
from services.privacy import PhotoError, clean_photo
from services.risk_engine import CATEGORIES

router = APIRouter(prefix="/api/reports", tags=["reports"])

# A moderator can only move a report along these paths.
ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "unverified": {"verified", "rejected"},
    "verified": {"resolved", "rejected"},
    "resolved": {"unverified"},  # reopen: the issue is back and needs checking again
    "rejected": {"unverified"},
}


# --------------------------------------------------------------------------------------------------
# Conversion helpers (also used by the dashboard)
# --------------------------------------------------------------------------------------------------


def confirmation_counts(db: Session, ids: list[str]) -> dict[str, ConfirmationCounts]:
    out: dict[str, ConfirmationCounts] = {}
    if not ids:
        return out
    rows = db.execute(
        select(ReportConfirmation.report_id, ReportConfirmation.still_present, func.count())
        .where(ReportConfirmation.report_id.in_(ids))
        .group_by(ReportConfirmation.report_id, ReportConfirmation.still_present)
    ).all()
    for rid, still, n in rows:
        c = out.setdefault(rid, ConfirmationCounts())
        if still:
            c.still_present = n
        else:
            c.fixed = n
    return out


def dispute_counts(db: Session, ids: list[str]) -> dict[str, int]:
    if not ids:
        return {}
    rows = db.execute(select(ReportDispute.report_id, func.count()).where(ReportDispute.report_id.in_(ids)).group_by(ReportDispute.report_id)).all()
    return {rid: n for rid, n in rows}


def to_public(r: Report, counts: dict[str, ConfirmationCounts], now: Optional[datetime] = None) -> ReportPublic:
    now = now or utcnow()
    # The free text of an unverified report is not shown publicly until a moderator has reviewed it.
    show_text = r.status in ("verified", "resolved") or r.source == "field_audit"
    return ReportPublic(
        id=r.id,
        category=r.category,
        label=CATEGORIES[r.category].label,
        lat=r.lat,
        lng=r.lng,
        description=(r.description or None) if show_text else None,
        status=r.status,
        source=r.source,
        simulated=r.simulated,
        reported_at=as_utc(r.reported_at),
        verified_at=as_utc(r.verified_at) if r.verified_at else None,
        resolved_at=as_utc(r.resolved_at) if r.resolved_at else None,
        age_days=round((as_utc(now) - as_utc(r.reported_at)).total_seconds() / 86_400, 1),
        confirmations=counts.get(r.id, ConfirmationCounts()),
    )


def to_admin(r: Report, counts: dict[str, ConfirmationCounts], now: Optional[datetime] = None, disputes: Optional[dict[str, int]] = None) -> ReportAdmin:
    base = to_public(r, counts, now).model_dump()
    base["description"] = r.description or None  # moderators see the text of every report
    return ReportAdmin(
        **base,
        classifier_category=r.classifier_category,
        classifier_confidence=r.classifier_confidence,
        category_source=r.category_source,
        possible_duplicate_of=r.possible_duplicate_of,
        reporter_id=r.reporter_id,
        has_photo=bool(r.has_photo or r.photo_url),
        disputes=(disputes or {}).get(r.id, 0),
    )


def nearby_candidates(db: Session, category: str, lat: float, lng: float, now: datetime) -> list[Candidate]:
    """Recent real reports of the same category close to a point (a coarse box; the precise rule runs after)."""
    d_lat = 60.0 / M_PER_DEG_LAT
    d_lng = 60.0 / max(1.0, m_per_deg_lng(lat))
    rows = db.scalars(
        select(Report).where(
            Report.category == category,
            Report.status != "rejected",
            Report.simulated.is_(False),
            Report.lat.between(lat - d_lat, lat + d_lat),
            Report.lng.between(lng - d_lng, lng + d_lng),
            Report.reported_at >= now - timedelta(days=WINDOW_DAYS + 1),
        )
    ).all()
    return [Candidate(r.id, r.category, r.lat, r.lng, r.reported_at, r.description or "") for r in rows]


def _suggestion(c) -> Suggestion:
    return Suggestion(category=c.category, confidence=c.confidence, method=c.method, needs_review=c.needs_review)


# --------------------------------------------------------------------------------------------------
# Public endpoints
# --------------------------------------------------------------------------------------------------


@router.post("/classify", response_model=ClassifyResponse, dependencies=[Depends(classify_limit)])
def classify(payload: ClassifyRequest) -> ClassifyResponse:
    """Suggest a category for a piece of text. Nothing is stored."""
    c = classify_text(payload.text)
    return ClassifyResponse(**_suggestion(c).model_dump(), probabilities=c.probabilities)


@router.post("", response_model=ReportCreated, status_code=201, dependencies=[Depends(report_limit)])
def create_report(payload: ReportCreate, request: Request, db: Session = Depends(get_db)) -> ReportCreated:
    who = caller(request)  # 401 if login is required and nobody is signed in
    now = utcnow()
    photo: Optional[bytes] = None
    if payload.photo:
        try:
            photo = clean_photo(payload.photo)  # re-encoded on the server: metadata never reaches the database
        except PhotoError as err:
            raise HTTPException(422, str(err)) from err
    suggestion = classify_text(payload.description)  # never raises; a classifier outage only means "needs review"

    category, category_source = payload.category, "user"
    if category is None:
        if suggestion.category and not suggestion.needs_review:
            category, category_source = suggestion.category, "classifier"
        else:
            raise HTTPException(
                422,
                detail={
                    "message": "Choose a category. The description was not clear enough to suggest one.",
                    "suggestion": _suggestion(suggestion).model_dump(),
                },
            )

    report_id = new_id()
    target = Candidate(report_id, category, payload.lat, payload.lng, now, payload.description)
    matches = find_matches(target, nearby_candidates(db, category, payload.lat, payload.lng, now))

    report = Report(
        id=report_id,
        category=category,
        lat=payload.lat,
        lng=payload.lng,
        description=payload.description,
        status="unverified",
        source="community",
        simulated=False,
        # The account id, or a salted one-way hash of the client, so one person cannot corroborate their own
        # report by filing it twice.
        reporter_id=who.reporter_id,
        photo=photo,
        has_photo=photo is not None,
        category_source=category_source,
        classifier_category=suggestion.category,
        classifier_confidence=suggestion.confidence,
        possible_duplicate_of=matches[0].id if matches else None,
        reported_at=now,
    )
    db.add(report)
    db.add(ReportEvent(report_id=report_id, action="created", actor=who.actor))
    db.commit()

    return ReportCreated(
        id=report_id,
        status="unverified",
        category=category,
        category_source=category_source,  # type: ignore[arg-type]
        suggestion=_suggestion(suggestion),
        similar_nearby=len(matches),
        photo_saved=photo is not None,
        message=(
            "Saved as unverified. It shows on the map but only counts toward a score once a moderator verifies it, "
            "or independent reports of the same issue nearby corroborate it."
        ),
    )


@router.get("", response_model=list[ReportPublic])
def list_reports(
    min_lat: Optional[float] = Query(None, ge=-90, le=90),
    max_lat: Optional[float] = Query(None, ge=-90, le=90),
    min_lng: Optional[float] = Query(None, ge=-180, le=180),
    max_lng: Optional[float] = Query(None, ge=-180, le=180),
    status: Optional[Literal["unverified", "verified", "resolved"]] = None,
    include_simulated: bool = False,
    limit: int = Query(200, ge=1, le=500),
    db: Session = Depends(get_db),
) -> list[ReportPublic]:
    """Public map data. Rejected reports are never listed; simulated ones only if asked for (and flagged)."""
    box = [min_lat, max_lat, min_lng, max_lng]
    if any(v is not None for v in box) and any(v is None for v in box):
        raise HTTPException(422, "Give all four of min_lat, max_lat, min_lng and max_lng, or none of them.")
    q = select(Report).where(Report.status != "rejected")
    if status:
        q = q.where(Report.status == status)
    if not include_simulated:
        q = q.where(Report.simulated.is_(False))
    if min_lat is not None:
        q = q.where(Report.lat.between(min_lat, max_lat), Report.lng.between(min_lng, max_lng))
    rows = db.scalars(q.order_by(Report.reported_at.desc()).limit(limit)).all()
    counts = confirmation_counts(db, [r.id for r in rows])
    now = utcnow()
    return [to_public(r, counts, now) for r in rows]


@router.post("/{report_id}/confirmations", response_model=ConfirmationCounts, status_code=201, dependencies=[Depends(confirm_limit)])
def confirm(report_id: str, payload: ConfirmationCreate, request: Request, db: Session = Depends(get_db)) -> ConfirmationCounts:
    """One tap: "still there" or "fixed". It informs moderators and raises the report in their queue.
    It never changes a score by itself."""
    who = caller(request)
    r = db.get(Report, report_id)
    if r is None or r.status == "rejected":
        raise HTTPException(404, "Report not found.")
    if who.account and r.reporter_id == who.reporter_id:
        raise HTTPException(403, "You cannot confirm your own report.")
    h = who.key
    existing = db.scalar(select(ReportConfirmation).where(ReportConfirmation.report_id == report_id, ReportConfirmation.client_hash == h))
    if existing:
        existing.still_present = payload.still_present  # someone may change their mind
    else:
        db.add(ReportConfirmation(report_id=report_id, still_present=payload.still_present, client_hash=h))
    db.commit()
    return confirmation_counts(db, [report_id]).get(report_id, ConfirmationCounts())


@router.post("/{report_id}/disputes", response_model=DisputeCounts, status_code=201, dependencies=[Depends(dispute_limit)])
def dispute(report_id: str, payload: DisputeCreate, request: Request, db: Session = Depends(get_db)) -> DisputeCounts:
    """"This report is wrong." Puts the report back in the moderators' queue. It never changes a score by itself."""
    who: Caller = caller(request)
    r = db.get(Report, report_id)
    if r is None or r.status == "rejected":
        raise HTTPException(404, "Report not found.")
    if who.account and r.reporter_id == who.reporter_id:
        raise HTTPException(403, "You cannot dispute your own report.")
    if db.scalar(select(ReportDispute).where(ReportDispute.report_id == report_id, ReportDispute.client_key == who.key)):
        raise HTTPException(409, "You have already disputed this report.")
    db.add(ReportDispute(report_id=report_id, reason=payload.reason, client_key=who.key))
    db.commit()
    return DisputeCounts(disputes=dispute_counts(db, [report_id]).get(report_id, 0))


# --------------------------------------------------------------------------------------------------
# Admin endpoints
# --------------------------------------------------------------------------------------------------


@router.patch("/{report_id}/status", response_model=ReportAdmin)
def change_status(report_id: str, payload: StatusChange, db: Session = Depends(get_db), actor: str = Depends(require_admin)) -> ReportAdmin:
    r = db.get(Report, report_id)
    if r is None:
        raise HTTPException(404, "Report not found.")
    allowed = ALLOWED_TRANSITIONS[r.status]
    if payload.status not in allowed:
        raise HTTPException(
            409,
            detail={"message": f"A {r.status} report can only move to: {', '.join(sorted(allowed))}.", "allowed": sorted(allowed)},
        )
    now = utcnow()
    r.status = payload.status
    if payload.status == "verified":
        r.verified_at = now
    elif payload.status == "resolved":
        r.resolved_at = now  # lets the dashboard show how long fixes take
    elif payload.status == "unverified":
        r.verified_at = None  # a reopened report must not keep a stale verification time
        r.resolved_at = None
    db.add(ReportEvent(report_id=r.id, action=f"status:{payload.status}", actor=actor, note=payload.note))
    if payload.status == "rejected" and (r.has_photo or r.photo_url):
        r.photo, r.has_photo, r.photo_url = None, False, None  # data minimisation: nothing is kept for a rejected report
        db.add(ReportEvent(report_id=r.id, action="photo:removed", actor=actor, note="deleted when the report was rejected"))
    db.commit()
    return to_admin(r, confirmation_counts(db, [r.id]), now, dispute_counts(db, [r.id]))


@router.post("/field-audit", response_model=ReportAdmin, status_code=201)
def field_audit(payload: FieldAuditCreate, db: Session = Depends(get_db), actor: str = Depends(require_admin)) -> ReportAdmin:
    """A report made by the team walking a corridor: saved as verified, with its own source label.
    Real field data on a small, dense area is more convincing than many invented reports."""
    now = utcnow()
    r = Report(
        category=payload.category,
        lat=payload.lat,
        lng=payload.lng,
        description=payload.description,
        status="verified",
        source="field_audit",
        simulated=False,
        reporter_id=f"field-audit:{payload.auditor or 'team'}",
        category_source="user",
        reported_at=now,
        verified_at=now,
    )
    db.add(r)
    db.flush()
    db.add(ReportEvent(report_id=r.id, action="created", actor=actor, note="field audit"))
    db.commit()
    return to_admin(r, {}, now)


@router.get("/{report_id}/events", response_model=list[EventOut])
def events(report_id: str, db: Session = Depends(get_db), _: str = Depends(require_admin)) -> list[EventOut]:
    """Audit trail: who did what, and when."""
    r = db.get(Report, report_id)
    if r is None:
        raise HTTPException(404, "Report not found.")
    return [EventOut(action=e.action, actor=e.actor, note=e.note, at=as_utc(e.at)) for e in r.events]


@router.get("/{report_id}/photo")
def photo(report_id: str, db: Session = Depends(get_db), _: str = Depends(require_admin)) -> Response:
    """The cleaned photo of a report. Moderators only: photos can show faces or number plates."""
    r = db.get(Report, report_id)
    if r is None or not r.photo:
        raise HTTPException(404, "This report has no photo.")
    return Response(
        content=r.photo,
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Content-Disposition": 'inline; filename="report-photo.jpg"'},
    )


@router.delete("/{report_id}/photo", response_model=ReportAdmin)
def remove_photo(
    report_id: str,
    note: Optional[str] = Query(default=None, max_length=280),
    db: Session = Depends(get_db),
    actor: str = Depends(require_admin),
) -> ReportAdmin:
    """Delete a photo, for example because it shows a face or a number plate. The report itself stays."""
    r = db.get(Report, report_id)
    if r is None:
        raise HTTPException(404, "Report not found.")
    if not (r.has_photo or r.photo_url):
        raise HTTPException(404, "This report has no photo.")
    r.photo, r.has_photo, r.photo_url = None, False, None
    db.add(ReportEvent(report_id=r.id, action="photo:removed", actor=actor, note=note))
    db.commit()
    return to_admin(r, confirmation_counts(db, [r.id]), None, dispute_counts(db, [r.id]))
