"""Route comparison."""
from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from database import get_db, utcnow
from models.infra import InfraFeature
from models.report import Report
from routes.deps import compare_limit
from schemas.route import (
    NoteOut,
    Place,
    RouteAnalysisOut,
    RouteCompareRequest,
    RouteCompareResponse,
    StabilityOut,
)
from services import daylight, routing
from services.explanation import explain
from services.geo import LatLng, bbox
from services.risk_engine import (
    BAND_TEXT,
    PERSONAS,
    Config,
    FeaturePoint,
    ReportPoint,
    RouteAnalysis,
    analyse_route,
    rank_stability,
)
from services.routing import RoutingError

router = APIRouter(prefix="/api/routes", tags=["routes"])

DISCLAIMER = (
    "PathLens compares reported street conditions. It does not predict crime and does not say that any route is "
    "safe. Missing information is not evidence that nothing is wrong."
)


async def _resolve(place: Place) -> LatLng:
    if place.query is not None:
        return await routing.geocode(place.query)
    return (place.lat, place.lng)  # type: ignore[return-value]


def _analysis_out(a: RouteAnalysis) -> RouteAnalysisOut:
    d = asdict(a)
    metrics = d["metrics"]
    metrics["concern_band_text"] = BAND_TEXT[metrics["concern_band"]]
    return RouteAnalysisOut.model_validate(
        {"name": a.name, "route": d["route"], "metrics": metrics, "weights": a.weights, "chunks": d["chunks"], "evidence": d["evidence"], "gaps": d["gaps"]}
    )


@router.post("/compare", response_model=RouteCompareResponse, dependencies=[Depends(compare_limit)])
async def compare(payload: RouteCompareRequest, db: Session = Depends(get_db)) -> RouteCompareResponse:
    """Compare up to three walking routes.

    Privacy: this endpoint only reads. Origin, destination and place names are used to fetch routes and
    are held only in a short-lived in-memory cache; they are never written to the database or a log.
    """
    cfg = Config()
    now = utcnow()
    try:
        origin = await _resolve(payload.origin)
        destination = await _resolve(payload.destination)
        routes = await routing.get_routes(origin, destination)
    except RoutingError as err:
        raise HTTPException(err.status, err.message) from err

    persona = PERSONAS[payload.persona]
    base_weights = dict(payload.weights) if payload.weights else dict(persona.weights)

    # Time of day: lighting counts in full after dark, less in daylight.
    mid = routes[0].coords[len(routes[0].coords) // 2]
    at = payload.at or now
    light = daylight.describe(mid[0], mid[1], at)

    all_coords = [c for r in routes for c in r.coords]
    s, w, n, e = bbox(all_coords, pad_m=cfg.buffer_m + 20)
    q = select(Report).where(Report.status != "rejected", Report.lat.between(s, n), Report.lng.between(w, e))
    if not payload.include_simulated:
        q = q.where(Report.simulated.is_(False))
    points = [
        ReportPoint(
            id=r.id, category=r.category, lat=r.lat, lng=r.lng, status=r.status, reported_at=r.reported_at,
            verified_at=r.verified_at, simulated=r.simulated, reporter_id=r.reporter_id, source=r.source,
        )
        for r in db.scalars(q)
    ]

    fs, fw, fn, fe = bbox(all_coords, pad_m=600.0)
    features = [
        FeaturePoint(f.kind, f.lat, f.lng)
        for f in db.scalars(select(InfraFeature).where(InfraFeature.lat.between(fs, fn), InfraFeature.lng.between(fw, fe)))
    ]

    analyses = [
        analyse_route(
            r, points, now, cfg,
            name=f"Route {'ABC'[i]}",
            weights=base_weights,
            severity_boost=persona.severity_boost,
            lighting_multiplier=light["lighting_weight_multiplier"],
            features=features,
        )
        for i, r in enumerate(routes)
    ]
    stability = rank_stability(analyses) if payload.sensitivity else None
    notes = explain(analyses, stability=stability, daylight=light)

    return RouteCompareResponse(
        routes=[_analysis_out(a) for a in analyses],
        notes=[NoteOut(**x) for x in notes],
        stability=StabilityOut(**asdict(stability)) if stability else None,
        daylight=light,
        params={
            "persona": payload.persona,
            "weights_requested": base_weights,
            "weight_share_applied": analyses[0].metrics.weight_share,
            "include_simulated": payload.include_simulated,
            "evaluated_at": at.isoformat(),
            "route_provider": routes[0].source,
            "weights_note": "Provisional weights, not a validated safety model.",
        },
        disclaimer=DISCLAIMER,
    )
