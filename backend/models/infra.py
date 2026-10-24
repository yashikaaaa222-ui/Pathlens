from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from database import Base, utcnow


class InfraFeature(Base):
    """A mapped feature from the OpenStreetMap baseline snapshot (lamp, help point or activity point)."""

    __tablename__ = "infra_features"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    osm_ref: Mapped[str] = mapped_column(String(32), unique=True, index=True)  # e.g. node/123
    kind: Mapped[str] = mapped_column(String(12), index=True)  # lamp | help | activity
    subtype: Mapped[str] = mapped_column(String(40))
    lat: Mapped[float] = mapped_column(Float, index=True)
    lng: Mapped[float] = mapped_column(Float, index=True)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
