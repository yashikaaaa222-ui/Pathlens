"""Data freshness and confidence.

Confidence is kept separate from the concern score: it says how much of a route has recent evidence,
not how many problems were reported. Little data lowers confidence; it never lowers concern.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, Protocol, Sequence

# Days until a report's weight halves. A broken light stays broken until it is repaired, while an
# obstruction is usually gone within days, so one global decay rate would be wrong for both.
HALF_LIFE_DAYS: dict[str, float] = {
    "streetlight": 90.0,
    "poor_lighting": 90.0,
    "obstruction": 7.0,
    "footpath": 120.0,
    "visibility": 14.0,
}

OBSERVE_DAYS = 180.0  # evidence older than this no longer counts as coverage
CONFIDENCE_HALF_LIFE_DAYS = 120.0
DAY_SECONDS = 86_400.0


class Dated(Protocol):
    category: str
    reported_at: datetime
    verified_at: Optional[datetime]


def as_utc(dt: datetime) -> datetime:
    """SQLite returns naive datetimes. Everything stored here is UTC, so treat naive values as UTC."""
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def reference_time(report: Dated) -> datetime:
    return as_utc(report.verified_at or report.reported_at)


def age_days(report: Dated, now: datetime) -> float:
    return max(0.0, (as_utc(now) - reference_time(report)).total_seconds() / DAY_SECONDS)


def decay(report: Dated, now: datetime) -> float:
    return 0.5 ** (age_days(report, now) / HALF_LIFE_DAYS[report.category])


def freshness_score(ages_days: Sequence[float]) -> float:
    """Mean recency of the evidence behind a route, from 0 (very old) to 1 (brand new)."""
    if not ages_days:
        return 0.0
    return sum(0.5 ** (a / CONFIDENCE_HALF_LIFE_DAYS) for a in ages_days) / len(ages_days)


def confidence(coverage: float, freshness: float) -> tuple[float, str]:
    value = coverage * (0.5 + 0.5 * freshness)
    label = "High" if value >= 0.6 else "Medium" if value >= 0.3 else "Low"
    return value, label
