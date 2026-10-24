from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

Persona = Literal["default", "night_walker", "step_free", "cyclist"]


class Place(BaseModel):
    """Either coordinates or a place name. A name is looked up server-side and never stored."""

    model_config = ConfigDict(extra="forbid")

    lat: Optional[float] = Field(default=None, ge=-90, le=90)
    lng: Optional[float] = Field(default=None, ge=-180, le=180)
    query: Optional[str] = Field(default=None, min_length=2, max_length=200)

    @model_validator(mode="after")
    def _one_form(self) -> "Place":
        has_coords = self.lat is not None and self.lng is not None
        if has_coords == (self.query is not None):
            raise ValueError("Give either lat and lng, or query, but not both.")
        if (self.lat is None) != (self.lng is None):
            raise ValueError("lat and lng must be given together.")
        return self


class RouteCompareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    origin: Place
    destination: Place
    persona: Persona = "default"
    # Optional custom weights, e.g. {"lighting": 60, "hazard": 40}. Overrides the persona's weights.
    weights: Optional[dict[Literal["lighting", "hazard"], float]] = None
    # When to evaluate time-of-day lighting. Defaults to now.
    at: Optional[datetime] = None
    # Simulated demo reports are excluded unless asked for, and are always flagged when included.
    include_simulated: bool = False
    # Check how often the best route changes when the weights are nudged.
    sensitivity: bool = True

    @model_validator(mode="after")
    def _weights_ok(self) -> "RouteCompareRequest":
        if self.weights is not None:
            if any(v < 0 or v > 100 for v in self.weights.values()):
                raise ValueError("Each weight must be between 0 and 100.")
            if sum(self.weights.values()) <= 0:
                raise ValueError("At least one weight must be above zero.")
        return self


class ChunkOut(BaseModel):
    i: int
    len: float
    observed: bool
    weight: float
    coords: list[tuple[float, float]]


class EvidenceOut(BaseModel):
    id: str
    category: str
    label: str
    status: str
    basis: str
    counted: bool
    weight: float
    chunk: int
    distance_m: float
    lat: float
    lng: float
    age_days: float
    simulated: bool
    source: str


class GapOut(BaseModel):
    from_m: int
    to_m: int
    length_m: int
    lat: float
    lng: float


class FactorOut(BaseModel):
    available: bool
    raw: float
    score: int


class MetricsOut(BaseModel):
    km: float
    factors: dict[str, FactorOut]
    weight_share: dict[str, int]
    concern_score: int
    concern_band: Literal["insufficient", "low", "moderate", "elevated"]
    concern_band_text: str
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
    informational: dict[str, dict[str, Any]]


class RouteOut(BaseModel):
    distance: float
    duration: float
    source: str
    coords: list[tuple[float, float]]


class RouteAnalysisOut(BaseModel):
    name: str
    route: RouteOut
    metrics: MetricsOut
    weights: dict[str, float]
    chunks: list[ChunkOut]
    evidence: list[EvidenceOut]
    gaps: list[GapOut] = []


class NoteOut(BaseModel):
    text: str
    ids: list[str] = []


class StabilityOut(BaseModel):
    trials: int
    jitter: float
    best_share: dict[str, float]
    ties: int
    top_share: float
    stable: bool
    label: str


class RouteCompareResponse(BaseModel):
    routes: list[RouteAnalysisOut]
    notes: list[NoteOut]
    stability: Optional[StabilityOut]
    daylight: dict[str, Any]
    params: dict[str, Any]
    disclaimer: str
