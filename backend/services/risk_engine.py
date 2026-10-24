"""PathLens concern engine.

It scores a route from reported street conditions. It does not predict crime and does not say a route
is safe. The names in this module say "concern" for that reason.

Design rules, each one a fix for a known weakness of this kind of tool:

* Unknown is not clear. A stretch with no evidence is unobserved, and a route with too little evidence
  gets the band "insufficient", never "low".
* Per-distance scoring. Routes are cut into ~100 m chunks and scored per kilometre, so a long route is
  not punished for being long, and the longest affected stretch is reported separately.
* Small-sample smoothing. A pseudo-length (`smoothing_km`) is added to the route length, so one report on a
  300 m route cannot saturate the score.
* Trust tiers. Verified reports count in full. Unverified reports count at half weight only when two
  independent reporters corroborate them in the same chunk. A single unverified report is shown, not counted.
  Unverified reports expire after `unverified_expiry_days`.
* Per-category freshness decay (see services/freshness.py).
* Uncertainty is shown. The score comes with a range ("if the unobserved part looked like the observed
  part") and a separate confidence value.
* Only lighting and road hazards are scored. Help access, mapped lamps and surrounding activity are
  informational layers from OpenStreetMap. They never enter the score, because missing map data and
  under-served areas would otherwise be penalised for things the road did not cause.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import datetime
from typing import Mapping, Optional, Sequence

from services import freshness
from services.geo import LatLng, bbox, chunk_route, dist_to_polyline

# --------------------------------------------------------------------------------------------------
# Vocabulary
# --------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Category:
    factor: str
    label: str
    severity: float


CATEGORIES: dict[str, Category] = {
    "streetlight": Category("lighting", "Broken streetlight", 1.0),
    "poor_lighting": Category("lighting", "Poorly lit stretch", 0.7),
    "obstruction": Category("hazard", "Road obstruction", 1.0),
    "footpath": Category("hazard", "Damaged footpath", 0.8),
    "visibility": Category("hazard", "Poor visibility", 0.6),
}

SCORED_FACTORS = ("lighting", "hazard")
FACTOR_LABELS = {"lighting": "Lighting", "hazard": "Road hazards"}
DEFAULT_WEIGHTS = {"lighting": 35.0, "hazard": 30.0}  # provisional, unvalidated

BAND_TEXT = {
    "insufficient": "Not enough data",
    "low": "Low reported concern",
    "moderate": "Moderate reported concern",
    "elevated": "Elevated reported concern",
}


@dataclass(frozen=True)
class Persona:
    key: str
    label: str
    description: str
    weights: Mapping[str, float]
    severity_boost: Mapping[str, float] = field(default_factory=dict)


PERSONAS: dict[str, Persona] = {
    "default": Persona("default", "Default", "Provisional preset: lighting 35, road hazards 30.", DEFAULT_WEIGHTS),
    "night_walker": Persona("night_walker", "Walking at night", "Lighting counts most.", {"lighting": 55.0, "hazard": 25.0}),
    "step_free": Persona(
        "step_free",
        "Wheelchair or stroller",
        "Footpath condition and obstructions count most.",
        {"lighting": 20.0, "hazard": 60.0},
        {"footpath": 1.5, "obstruction": 1.2},
    ),
    "cyclist": Persona(
        "cyclist",
        "Cycling",
        "Road obstructions and surface count most.",
        {"lighting": 30.0, "hazard": 55.0},
        {"obstruction": 1.2},
    ),
}


@dataclass(frozen=True)
class Config:
    chunk_m: float = 100.0  # routes are split into chunks of this length
    buffer_m: float = 30.0  # a report counts toward a chunk if it is this close
    steepness: float = 3.0  # weighted issues per km at which a factor reaches ~63/100 (provisional)
    smoothing_km: float = 0.25  # pseudo-length added to every route (small-sample smoothing)
    observe_days: float = freshness.OBSERVE_DAYS
    unverified_expiry_days: float = 60.0
    insufficient_below: float = 0.2  # coverage below this means "not enough data"
    band_low_below: int = 20
    band_moderate_below: int = 45
    help_radius_m: float = 400.0
    lamp_radius_m: float = 25.0
    activity_radius_m: float = 100.0
    min_baseline_features: int = 5  # fewer mapped features than this and the layer is "unavailable"
    gap_min_m: float = 200.0  # a stretch with no recent evidence is listed as a gap from this length
    weights: Mapping[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))


# --------------------------------------------------------------------------------------------------
# Inputs
# --------------------------------------------------------------------------------------------------


@dataclass
class ReportPoint:
    id: str
    category: str
    lat: float
    lng: float
    status: str  # unverified | verified | resolved | rejected
    reported_at: datetime
    verified_at: Optional[datetime] = None
    simulated: bool = False
    reporter_id: Optional[str] = None
    source: str = "community"


@dataclass
class FeaturePoint:
    kind: str  # lamp | help | activity
    lat: float
    lng: float


@dataclass
class RouteGeom:
    coords: list[LatLng]
    distance: float  # metres
    duration: float  # seconds
    source: str = ""


# --------------------------------------------------------------------------------------------------
# Outputs
# --------------------------------------------------------------------------------------------------


@dataclass
class Chunk:
    i: int
    coords: list[LatLng]
    len: float
    weight: float = 0.0
    observed: bool = False


@dataclass
class Evidence:
    id: str
    category: str
    label: str
    status: str
    basis: str  # verified | corroborated | unverified | resolved | rejected
    counted: bool
    weight: float
    chunk: int
    distance_m: float
    lat: float
    lng: float
    age_days: float
    simulated: bool
    source: str


@dataclass
class Gap:
    """A stretch of the route with no recent evidence. It is unknown, not clear: a place where one report or
    one "still there" tap from someone walking it would add the most."""

    from_m: int  # distance along the route where the gap starts
    to_m: int
    length_m: int
    lat: float  # a point in the middle of the gap, for a map marker
    lng: float


@dataclass
class FactorScore:
    available: bool
    raw: float = 0.0
    score: int = 0


@dataclass
class Metrics:
    km: float
    factors: dict[str, FactorScore]
    weight_share: dict[str, int]
    concern_score: int
    concern_band: str
    concern_range: Optional[tuple[int, int]]
    coverage: float
    confidence_value: float
    confidence_label: str
    last_update_days: Optional[int]
    longest_stretch_m: int
    counted: int
    counts: dict[str, int]
    simulated: bool
    expired_unverified: int
    informational: dict[str, dict]


@dataclass
class RouteAnalysis:
    name: str
    route: RouteGeom
    chunks: list[Chunk]
    evidence: list[Evidence]
    metrics: Metrics
    factor_exact: dict[str, float]  # un-rounded 0-100 factor scores, used by rank_stability
    weights: dict[str, float]  # effective weights actually applied (persona, custom, time of day)
    gaps: list[Gap] = field(default_factory=list)  # longest first


@dataclass
class Stability:
    trials: int
    jitter: float
    best_share: dict[str, float]
    ties: int
    top_share: float
    stable: bool
    label: str  # "stable" | "close call"


# --------------------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------------------


def round_half_up(x: float) -> int:
    """Round halves up (Python's round() rounds halves to even, which surprises people reading scores)."""
    return int(math.floor(x + 0.5))


def effective_weights(base: Mapping[str, float], lighting_multiplier: float = 1.0) -> dict[str, float]:
    w = {f: max(0.0, float(base.get(f, 0.0))) for f in SCORED_FACTORS}
    w["lighting"] *= max(0.0, lighting_multiplier)
    if sum(w.values()) <= 0:
        w = dict(DEFAULT_WEIGHTS)
    return w


def factor_score(raw: float, km: float, cfg: Config) -> float:
    density = raw / (km + cfg.smoothing_km)
    return 100.0 * (1.0 - math.exp(-density / cfg.steepness))


def _chunk_box(chunk: Chunk, pad_m: float) -> tuple[float, float, float, float]:
    return bbox(chunk.coords, pad_m)


def _covered_chunks(chunks: Sequence[Chunk], points: Sequence[LatLng], radius_m: float) -> list[bool]:
    """For each chunk, is any of the points within radius_m of it?"""
    flags = [False] * len(chunks)
    for i, c in enumerate(chunks):
        s, w, n, e = _chunk_box(c, radius_m)
        for p in points:
            if s <= p[0] <= n and w <= p[1] <= e and dist_to_polyline(p, c.coords) <= radius_m:
                flags[i] = True
                break
    return flags


def _longest_gap(chunks: Sequence[Chunk], covered: Sequence[bool]) -> int:
    run = 0.0
    best = 0.0
    for c, ok in zip(chunks, covered):
        run = 0.0 if ok else run + c.len
        best = max(best, run)
    return round_half_up(best)


def find_gaps(chunks: Sequence[Chunk], min_len_m: float) -> list[Gap]:
    """Runs of unobserved chunks at least `min_len_m` long, longest first."""
    gaps: list[Gap] = []
    run: list[Chunk] = []
    start = pos = 0.0

    def close() -> None:
        length = sum(c.len for c in run)
        if run and length >= min_len_m:
            mid = run[len(run) // 2]
            a, b = mid.coords[0], mid.coords[-1]
            gaps.append(Gap(round_half_up(start), round_half_up(start + length), round_half_up(length), (a[0] + b[0]) / 2, (a[1] + b[1]) / 2))

    for c in chunks:
        if c.observed:
            close()
            run = []
        else:
            if not run:
                start = pos
            run.append(c)
        pos += c.len
    close()
    return sorted(gaps, key=lambda g: -g.length_m)


def _informational(route: RouteGeom, chunks: Sequence[Chunk], km: float, features: Optional[Sequence[FeaturePoint]], cfg: Config) -> dict[str, dict]:
    """Layers from the OpenStreetMap baseline. Shown to the user, never part of the score."""
    feats = list(features or [])
    by_kind = {k: [(f.lat, f.lng) for f in feats if f.kind == k] for k in ("help", "lamp", "activity")}
    ok = {k: len(v) >= cfg.min_baseline_features for k, v in by_kind.items()}
    note_unavailable = "Not enough mapped data loaded for this area. Treat it as unknown."

    help_layer: dict = {"available": ok["help"], "radius_m": cfg.help_radius_m, "scored": False}
    if ok["help"]:
        help_layer["nearest_m"] = round_half_up(min(dist_to_polyline(p, route.coords) for p in by_kind["help"]))
        help_layer["gap_m"] = _longest_gap(chunks, _covered_chunks(chunks, by_kind["help"], cfg.help_radius_m))
        help_layer["note"] = "Listed help points (police, hospital, clinic, pharmacy, fire station) from OpenStreetMap. Informational only; it does not change the score."
    else:
        help_layer["note"] = note_unavailable

    lamp_layer: dict = {"available": ok["lamp"], "radius_m": cfg.lamp_radius_m, "scored": False}
    if ok["lamp"]:
        near = sum(1 for p in by_kind["lamp"] if dist_to_polyline(p, route.coords) <= cfg.lamp_radius_m)
        lamp_layer["per_km"] = round(near / max(km, 0.05), 1)
        lamp_layer["mapped_gap_m"] = _longest_gap(chunks, _covered_chunks(chunks, by_kind["lamp"], cfg.lamp_radius_m))
        lamp_layer["note"] = "Mapped streetlights from OpenStreetMap. Coverage varies a lot, so a low number can mean missing map data, not missing lights. It does not change the score."
    else:
        lamp_layer["note"] = note_unavailable

    act_layer: dict = {"available": ok["activity"], "radius_m": cfg.activity_radius_m, "scored": False}
    if ok["activity"]:
        act_layer["gap_m"] = _longest_gap(chunks, _covered_chunks(chunks, by_kind["activity"], cfg.activity_radius_m))
        act_layer["note"] = "Low surrounding activity, estimated from mapped shops and amenities (an OpenStreetMap proxy). It does not change the score."
    else:
        act_layer["note"] = note_unavailable

    return {"help": help_layer, "lamps": lamp_layer, "activity": act_layer}


# --------------------------------------------------------------------------------------------------
# The analysis
# --------------------------------------------------------------------------------------------------


def analyse_route(
    route: RouteGeom,
    reports: Sequence[ReportPoint],
    now: datetime,
    cfg: Config = Config(),
    *,
    name: str = "",
    weights: Optional[Mapping[str, float]] = None,
    severity_boost: Optional[Mapping[str, float]] = None,
    lighting_multiplier: float = 1.0,
    features: Optional[Sequence[FeaturePoint]] = None,
) -> RouteAnalysis:
    chunks = [Chunk(i, list(coords), length) for i, (coords, length) in enumerate(chunk_route(route.coords, cfg.chunk_m))]
    total = max(1.0, sum(c.len for c in chunks))
    km = total / 1000.0
    boost = severity_boost or {}
    eff = effective_weights(weights or cfg.weights, lighting_multiplier)

    # 1. Attach each report to its nearest chunk, if it is inside the buffer. Stale unverified reports expire.
    links: list[tuple[ReportPoint, int, float]] = []
    expired = 0
    for r in reports:
        if r.category not in CATEGORIES:
            continue
        if r.status == "unverified" and freshness.age_days(r, now) > cfg.unverified_expiry_days:
            expired += 1
            continue
        best_i, best_d = -1, math.inf
        for c in chunks:
            d = dist_to_polyline((r.lat, r.lng), c.coords)
            if d < best_d:
                best_i, best_d = c.i, d
        if best_i >= 0 and best_d <= cfg.buffer_m:
            links.append((r, best_i, best_d))

    # 2. Corroboration needs two independent reporters in the same chunk and category. Without a
    #    reporter id every report counts as its own reporter.
    unverified: dict[tuple[int, str], set[str]] = {}
    for r, ci, _ in links:
        if r.status == "unverified":
            unverified.setdefault((ci, r.category), set()).add(r.reporter_id or r.id)

    # 3. Weight: verified 1, corroborated 0.5, everything else 0 (shown, not counted).
    evidence: list[Evidence] = []
    for r, ci, dist in links:
        cat = CATEGORIES[r.category]
        corroborated = r.status == "unverified" and len(unverified[(ci, r.category)]) >= 2
        status_weight = 1.0 if r.status == "verified" else 0.5 if corroborated else 0.0
        weight = status_weight * cat.severity * boost.get(r.category, 1.0) * freshness.decay(r, now)
        evidence.append(
            Evidence(
                id=r.id,
                category=r.category,
                label=cat.label,
                status=r.status,
                basis="corroborated" if corroborated else r.status,
                counted=weight > 0,
                weight=weight,
                chunk=ci,
                distance_m=round(dist, 1),
                lat=r.lat,
                lng=r.lng,
                age_days=freshness.age_days(r, now),
                simulated=r.simulated,
                source=r.source,
            )
        )

    # 4. Coverage: a chunk is observed if fresh, non-rejected evidence sits on or next to it.
    live = [e for e in evidence if e.status != "rejected" and e.age_days <= cfg.observe_days]
    for e in live:
        for j in (e.chunk - 1, e.chunk, e.chunk + 1):
            if 0 <= j < len(chunks):
                chunks[j].observed = True

    counted = [e for e in evidence if e.counted]
    raw: dict[str, float] = {}
    counts: dict[str, int] = {}
    for e in counted:
        chunks[e.chunk].weight += e.weight
        factor = CATEGORIES[e.category].factor
        raw[factor] = raw.get(factor, 0.0) + e.weight
        counts[e.category] = counts.get(e.category, 0) + 1

    # 5. Factor scores (0-100) and the weighted concern score.
    exact = {f: factor_score(raw.get(f, 0.0), km, cfg) for f in SCORED_FACTORS}
    factors = {f: FactorScore(True, raw.get(f, 0.0), round_half_up(exact[f])) for f in SCORED_FACTORS}
    wsum = sum(eff.values())
    score_exact = sum(exact[f] * eff[f] for f in SCORED_FACTORS) / wsum
    score = round_half_up(score_exact)

    # 6. Confidence is separate from concern: how much of the route has recent evidence.
    observed_len = sum(c.len for c in chunks if c.observed)
    coverage = observed_len / total
    fresh = freshness.freshness_score([e.age_days for e in live])
    conf_value, conf_label = freshness.confidence(coverage, fresh)
    last_update = round_half_up(min(e.age_days for e in live)) if live else None

    # 7. Uncertainty range: what the score would be if the unobserved part looked like the observed part.
    observed_km = observed_len / 1000.0
    concern_range: Optional[tuple[int, int]] = None
    if observed_km >= 0.05:
        scale = km / observed_km
        high = sum(factor_score(raw.get(f, 0.0) * scale, km, cfg) * eff[f] for f in SCORED_FACTORS) / wsum
        concern_range = (score, max(score, round_half_up(high)))

    run = longest = 0.0
    for c in chunks:
        run = run + c.len if c.weight > 0 else 0.0
        longest = max(longest, run)

    # Too little evidence means "unknown", never "low concern".
    if coverage < cfg.insufficient_below:
        band = "insufficient"
    elif score < cfg.band_low_below:
        band = "low"
    elif score < cfg.band_moderate_below:
        band = "moderate"
    else:
        band = "elevated"

    metrics = Metrics(
        km=km,
        factors=factors,
        weight_share={f: round_half_up(100 * eff[f] / wsum) for f in SCORED_FACTORS},
        concern_score=score,
        concern_band=band,
        concern_range=concern_range,
        coverage=coverage,
        confidence_value=conf_value,
        confidence_label=conf_label,
        last_update_days=last_update,
        longest_stretch_m=round_half_up(longest),
        counted=len(counted),
        counts=counts,
        simulated=any(e.simulated for e in evidence),
        expired_unverified=expired,
        informational=_informational(route, chunks, km, features, cfg),
    )
    return RouteAnalysis(
        name=name, route=route, chunks=chunks, evidence=evidence, metrics=metrics, factor_exact=exact, weights=eff,
        gaps=find_gaps(chunks, cfg.gap_min_m),
    )


def rank_stability(
    analyses: Sequence[RouteAnalysis], *, trials: int = 200, jitter: float = 0.5, seed: int = 7
) -> Optional[Stability]:
    """How often does the best route change when the weights are nudged?

    Each trial scales every weight by a random factor in [1 - jitter, 1 + jitter] and notes which usable
    route has the lowest concern. A winner needs to lead by at least one point; otherwise the trial is a
    tie. A low top share means the comparison is a close call, and the interface should say so.
    """
    usable = [a for a in analyses if a.metrics.concern_band != "insufficient"]
    if len(usable) < 2:
        return None
    rng = random.Random(seed)
    base = usable[0].weights
    wins = {a.name: 0 for a in usable}
    ties = 0
    for _ in range(trials):
        w = {f: base[f] * rng.uniform(1 - jitter, 1 + jitter) for f in SCORED_FACTORS}
        tot = sum(w.values()) or 1.0
        scored = sorted(((sum(a.factor_exact[f] * w[f] for f in SCORED_FACTORS) / tot, a.name) for a in usable))
        if scored[1][0] - scored[0][0] < 1.0:
            ties += 1
        else:
            wins[scored[0][1]] += 1
    shares = {n: round(c / trials, 3) for n, c in wins.items()}
    top = max(shares.values())
    stable = top >= 0.8
    return Stability(trials, jitter, shares, ties, top, stable, "stable" if stable else "close call")
