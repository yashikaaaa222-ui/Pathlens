"""Duplicate detection for reports.

Rule-first: two reports are likely duplicates when they have the same category, sit within 40 m of
each other and were made within 30 days of each other. Text similarity (TF-IDF cosine) is a tiebreaker
that ranks the matches; it never rejects a rule match, because descriptions are short.

This only flags. A moderator decides whether to merge, verify or reject.

A flagged pair is also the raw material for corroboration, but corroboration in the score needs two
independent reporters (see services/risk_engine.py). A burst of reports from one person is a duplicate
cluster, not independent evidence.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Optional

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from services.freshness import as_utc
from services.geo import haversine

RADIUS_M = 40.0
WINDOW_DAYS = 30.0


@dataclass(frozen=True)
class Candidate:
    id: str
    category: str
    lat: float
    lng: float
    reported_at: datetime
    description: str = ""


@dataclass(frozen=True)
class DuplicateMatch:
    id: str
    distance_m: float
    days_apart: float
    text_similarity: Optional[float]


def _rule_match(a: Candidate, b: Candidate, radius_m: float, window_days: float) -> Optional[tuple[float, float]]:
    if a.id == b.id or a.category != b.category:
        return None
    dist = haversine((a.lat, a.lng), (b.lat, b.lng))
    days = abs((as_utc(a.reported_at) - as_utc(b.reported_at)).total_seconds()) / 86_400.0
    if dist <= radius_m and days <= window_days:
        return dist, days
    return None


def _similarities(target_text: str, texts: list[str]) -> list[Optional[float]]:
    """Cosine similarity of the target's text to each of the others, or None when text is missing."""
    if not target_text.strip() or not any(t.strip() for t in texts):
        return [None] * len(texts)
    try:
        vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4), lowercase=True)
        m = vec.fit_transform([target_text] + texts)
        sims = cosine_similarity(m[0], m[1:])[0]
    except ValueError:  # empty vocabulary
        return [None] * len(texts)
    return [round(float(s), 3) if t.strip() else None for s, t in zip(sims, texts)]


def find_matches(
    target: Candidate,
    others: Iterable[Candidate],
    *,
    radius_m: float = RADIUS_M,
    window_days: float = WINDOW_DAYS,
) -> list[DuplicateMatch]:
    hits: list[tuple[Candidate, float, float]] = []
    for o in others:
        m = _rule_match(target, o, radius_m, window_days)
        if m:
            hits.append((o, m[0], m[1]))
    if not hits:
        return []
    sims = _similarities(target.description, [o.description for o, _, _ in hits])
    matches = [DuplicateMatch(o.id, round(d, 1), round(days, 1), s) for (o, d, days), s in zip(hits, sims)]
    return sorted(matches, key=lambda m: (-(m.text_similarity or 0.0), m.distance_m))


def duplicate_map(reports: list[Candidate], *, radius_m: float = RADIUS_M, window_days: float = WINDOW_DAYS) -> dict[str, list[str]]:
    """{report id: [ids of likely duplicates]}, for the moderation queue."""
    out: dict[str, list[str]] = {}
    for a in reports:
        twins = [b.id for b in reports if _rule_match(a, b, radius_m, window_days)]
        if twins:
            out[a.id] = twins
    return out
