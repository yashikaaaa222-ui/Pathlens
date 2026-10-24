"""Data retention: keep only what is still useful, and say how long in the published methodology.

Rules (each can be changed with an environment variable):

* A rejected report is deleted 30 days after it was rejected (`RETENTION_REJECTED_DAYS`). The 30 days leave
  time for a moderator to reopen it by mistake.
* A report that was never reviewed is deleted after 180 days (`RETENTION_UNVERIFIED_DAYS`). It stopped
  counting toward scores after 60 days anyway.
* The photo of a resolved report is deleted 30 days after it was resolved (`RETENTION_PHOTO_DAYS`).
  A photo is also deleted at once when its report is rejected.
* Simulated demo reports are not touched here. They have their own switch (DELETE /api/admin/demo-seed).

Deleting a report deletes its audit events, confirmations and disputes with it.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from database import utcnow
from models.report import Report


def _days(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except ValueError:
        return default


@dataclass(frozen=True)
class RetentionPolicy:
    rejected_days: int
    unverified_days: int
    photo_days: int

    @classmethod
    def from_env(cls) -> "RetentionPolicy":
        return cls(
            rejected_days=_days("RETENTION_REJECTED_DAYS", 30),
            unverified_days=_days("RETENTION_UNVERIFIED_DAYS", 180),
            photo_days=_days("RETENTION_PHOTO_DAYS", 30),
        )

    def describe(self) -> list[str]:
        return [
            f"Rejected reports are deleted {self.rejected_days} days after rejection, and their photo is deleted at once.",
            f"Reports that were never reviewed are deleted after {self.unverified_days} days.",
            f"The photo of a resolved report is deleted {self.photo_days} days after it was resolved.",
            "Deleting a report also deletes its confirmations, disputes and audit events.",
        ]


def purge(db: Session, now: Optional[datetime] = None, policy: Optional[RetentionPolicy] = None) -> dict[str, int]:
    """Apply the retention rules to real (non-simulated) reports. Returns what was removed."""
    now = now or utcnow()
    policy = policy or RetentionPolicy.from_env()
    real = Report.simulated.is_(False)

    rejected = db.scalars(select(Report).where(real, Report.status == "rejected", Report.updated_at < now - timedelta(days=policy.rejected_days))).all()
    stale = db.scalars(select(Report).where(real, Report.status == "unverified", Report.reported_at < now - timedelta(days=policy.unverified_days))).all()
    for r in [*rejected, *stale]:
        db.delete(r)

    photos = db.scalars(
        select(Report).where(real, Report.status == "resolved", Report.has_photo.is_(True), Report.updated_at < now - timedelta(days=policy.photo_days))
    ).all()
    for r in photos:
        r.photo, r.has_photo, r.photo_url = None, False, None

    db.commit()
    return {"rejected_deleted": len(rejected), "unreviewed_deleted": len(stale), "photos_removed": len(photos)}
