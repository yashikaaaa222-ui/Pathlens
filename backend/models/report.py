from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, LargeBinary, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base, utcnow


def new_id() -> str:
    return uuid.uuid4().hex


class Report(Base):
    __tablename__ = "reports"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=new_id)
    category: Mapped[str] = mapped_column(String(32), index=True)
    lat: Mapped[float] = mapped_column(Float, index=True)
    lng: Mapped[float] = mapped_column(Float, index=True)
    description: Mapped[str] = mapped_column(String(280), default="")

    # unverified | verified | resolved | rejected
    status: Mapped[str] = mapped_column(String(16), default="unverified", index=True)
    # community | field_audit | simulated. Real data and demo data are never mixed up.
    source: Mapped[str] = mapped_column(String(16), default="community", index=True)
    simulated: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    # Corroboration needs two distinct reporters. "user:<id>" for a signed-in account, otherwise "anon:" plus
    # a salted hash of the client. Set PRIVACY_SALT in production, otherwise the anonymous hashes change on
    # every restart.
    reporter_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    # A cleaned JPEG (no EXIF, no GPS), kept only for moderators and never served publicly. `deferred` keeps
    # the bytes out of ordinary queries; `has_photo` answers "is there one?" without loading them.
    photo: Mapped[Optional[bytes]] = mapped_column(LargeBinary, nullable=True, deferred=True)
    has_photo: Mapped[bool] = mapped_column(Boolean, default=False)
    # Kept for a future move of photos to object storage.
    photo_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    category_source: Mapped[str] = mapped_column(String(12), default="user")  # user | classifier
    classifier_category: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    classifier_confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    possible_duplicate_of: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)

    reported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    events: Mapped[list["ReportEvent"]] = relationship(back_populates="report", cascade="all, delete-orphan", order_by="ReportEvent.id")
    confirmations: Mapped[list["ReportConfirmation"]] = relationship(back_populates="report", cascade="all, delete-orphan")
    disputes: Mapped[list["ReportDispute"]] = relationship(back_populates="report", cascade="all, delete-orphan")


class ReportEvent(Base):
    """Audit trail: every creation and status change, with who did it and when."""

    __tablename__ = "report_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    report_id: Mapped[str] = mapped_column(ForeignKey("reports.id", ondelete="CASCADE"), index=True)
    action: Mapped[str] = mapped_column(String(40))  # created | status:verified | ...
    actor: Mapped[str] = mapped_column(String(32))  # public | admin | system
    note: Mapped[Optional[str]] = mapped_column(String(280), nullable=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    report: Mapped[Report] = relationship(back_populates="events")


class ReportConfirmation(Base):
    """A "still there" / "fixed" tap on an existing report.

    It refreshes what moderators know and raises the report in their queue, but it never changes a score
    by itself: a human verifies. `client_hash` is a salted one-way hash used only to stop one browser
    voting repeatedly; no IP address is stored.
    """

    __tablename__ = "report_confirmations"
    __table_args__ = (UniqueConstraint("report_id", "client_hash", name="uq_confirmation_client"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    report_id: Mapped[str] = mapped_column(ForeignKey("reports.id", ondelete="CASCADE"), index=True)
    still_present: Mapped[bool] = mapped_column(Boolean)
    client_hash: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    report: Mapped[Report] = relationship(back_populates="confirmations")


class ReportDispute(Base):
    """Someone says a report is wrong. It never changes a score; it puts the report back in the moderators' queue.

    `client_key` is a 16-character one-way hash of the account or client, used only so one person cannot
    dispute the same report repeatedly. The reason text is screened for contact details like any other text.
    """

    __tablename__ = "report_disputes"
    __table_args__ = (UniqueConstraint("report_id", "client_key", name="uq_dispute_client"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    report_id: Mapped[str] = mapped_column(ForeignKey("reports.id", ondelete="CASCADE"), index=True)
    reason: Mapped[str] = mapped_column(String(280))
    client_key: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    report: Mapped[Report] = relationship(back_populates="disputes")
