from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from schemas.report import ReportAdmin


class StatsOut(BaseModel):
    total: int
    by_status: dict[str, int]
    by_category: dict[str, int]
    by_source: dict[str, int]
    simulated: int
    pending: int
    pending_over_7_days: int
    oldest_pending_days: Optional[float]
    median_verified_age_days: Optional[float]
    median_days_to_resolve: Optional[float] = None
    disputed: int = 0
    with_photo: int = 0
    possible_duplicates: int
    confirmations: dict[str, int]
    classifier: dict[str, Any]
    baseline: dict[str, Any]
    warnings: list[str]


class QueueItem(BaseModel):
    report: ReportAdmin
    priority: float
    reasons: list[str]
    similar_nearby: int
    kind: Literal["pending", "flagged"] = "pending"


class BaselineBox(BaseModel):
    model_config = ConfigDict(extra="forbid")
    south: float
    west: float
    north: float
    east: float


class BaselineLoad(BaseModel):
    """A saved Overpass JSON response (the object with an `elements` list)."""

    elements: list[dict[str, Any]] = Field(max_length=50_000)


class LatLngIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)


class DemoSeedRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    origin: LatLngIn
    destination: LatLngIn
