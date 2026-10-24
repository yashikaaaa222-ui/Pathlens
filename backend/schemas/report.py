from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from services.privacy import PII_MESSAGE, contains_personal_info

Category = Literal["streetlight", "poor_lighting", "obstruction", "footpath", "visibility"]
Status = Literal["unverified", "verified", "resolved", "rejected"]
Source = Literal["community", "field_audit", "simulated"]

CONSENT_MESSAGE = (
    "Please confirm that you have read the privacy notice (GET /api/methodology, section privacy): your report is "
    "stored with its location, reviewed by moderators, and removed under the published retention rules."
)


# Reports describe infrastructure, not people: refuse contact details, links and vehicle numbers in free text.
def _clean_description(v: str) -> str:
    v = (v or "").strip()
    if contains_personal_info(v):
        raise ValueError(PII_MESSAGE)
    return v


class ReportCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Optional: if it is left out, the classifier fills it in when it is confident enough.
    category: Optional[Category] = None
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    description: str = Field(default="", max_length=280)
    # An optional photo as a data URL (JPEG, PNG or WebP). The server rebuilds it without metadata and only
    # moderators can ever see it.
    photo: Optional[str] = Field(default=None, max_length=300_000)
    # Must be true. The interface shows the privacy notice next to the checkbox.
    consent: bool = Field(default=False, validate_default=True)

    @field_validator("description")
    @classmethod
    def _check_description(cls, v: str) -> str:
        return _clean_description(v)

    @field_validator("consent")
    @classmethod
    def _check_consent(cls, v: bool) -> bool:
        if v is not True:
            raise ValueError(CONSENT_MESSAGE)
        return v


class FieldAuditCreate(BaseModel):
    """A report made by the team walking a corridor. Saved as verified, with source "field_audit"."""

    model_config = ConfigDict(extra="forbid")

    category: Category
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    description: str = Field(default="", max_length=280)
    auditor: Optional[str] = Field(default=None, max_length=40)

    @field_validator("description")
    @classmethod
    def _check_description(cls, v: str) -> str:
        return _clean_description(v)


class Suggestion(BaseModel):
    category: Optional[Category] = None
    confidence: Optional[float] = None
    method: Literal["model", "rules", "none"] = "none"
    needs_review: bool = True


class ClassifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(max_length=280)


class ClassifyResponse(Suggestion):
    probabilities: dict[str, float] = {}
    note: str = "A suggestion only. The classifier is trained on a small synthetic seed set."


class ReportCreated(BaseModel):
    id: str
    status: Status
    category: Category
    category_source: Literal["user", "classifier"]
    suggestion: Suggestion
    similar_nearby: int
    photo_saved: bool = False
    message: str


class ConfirmationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    still_present: bool


class ConfirmationCounts(BaseModel):
    still_present: int = 0
    fixed: int = 0


class DisputeCreate(BaseModel):
    """"This report is wrong." It goes to the moderators; it never changes a score by itself."""

    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=5, max_length=280)

    @field_validator("reason")
    @classmethod
    def _check_reason(cls, v: str) -> str:
        return _clean_description(v)


class DisputeCounts(BaseModel):
    disputes: int


class ReportPublic(BaseModel):
    """What anyone may see. No photo, no reporter, no classifier internals. The free text of an
    unverified report is withheld until a moderator has reviewed it."""

    id: str
    category: Category
    label: str
    lat: float
    lng: float
    description: Optional[str]
    status: Status
    source: Source
    simulated: bool
    reported_at: datetime
    verified_at: Optional[datetime]
    resolved_at: Optional[datetime] = None
    age_days: float
    confirmations: ConfirmationCounts


class ReportAdmin(ReportPublic):
    classifier_category: Optional[str] = None
    classifier_confidence: Optional[float] = None
    category_source: str = "user"
    possible_duplicate_of: Optional[str] = None
    reporter_id: Optional[str] = None
    has_photo: bool = False
    disputes: int = 0


class StatusChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Status
    note: Optional[str] = Field(default=None, max_length=280)

    @model_validator(mode="after")
    def _rejection_needs_a_reason(self) -> "StatusChange":
        # Rejecting hides a report from every score, so the audit trail must say why.
        if self.status == "rejected" and not (self.note or "").strip():
            raise ValueError("Say why the report is rejected.")
        return self


class EventOut(BaseModel):
    action: str
    actor: str
    note: Optional[str]
    at: datetime
