"""Database engine and session.

Defaults to a local SQLite file so the API runs with no setup. For Supabase, set DATABASE_URL to the
Postgres connection string and install psycopg (see requirements.txt). Spatial matching is done in
Python at prototype scale, so PostGIS is not required yet.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Iterator, Optional

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

load_dotenv()


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def database_url() -> str:
    url = os.getenv("DATABASE_URL", "sqlite:///./pathlens.db")
    # Supabase and Heroku hand out "postgres://" or "postgresql://"; SQLAlchemy needs a driver name.
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


def make_engine(url: Optional[str] = None) -> Engine:
    url = url or database_url()
    if url.startswith("sqlite"):
        kwargs: dict = {"connect_args": {"check_same_thread": False}}
        if url in ("sqlite://", "sqlite:///:memory:"):
            kwargs["poolclass"] = StaticPool  # one shared in-memory database for every thread
        return create_engine(url, **kwargs)
    return create_engine(url, pool_pre_ping=True)


engine = make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def ensure_columns(bind: Optional[Engine] = None) -> list[str]:
    """Add columns that the models have but an older database file lacks.

    `create_all` only creates missing tables. Without this, a database made by an earlier version would fail
    on the first query that mentions a new column. It only ever adds nullable columns, so it cannot lose data.
    Use real migrations (Alembic) once the schema settles.
    """
    from sqlalchemy import inspect, text

    bind = bind or engine
    inspector = inspect(bind)
    added: list[str] = []
    for table in Base.metadata.sorted_tables:
        if not inspector.has_table(table.name):
            continue
        have = {c["name"] for c in inspector.get_columns(table.name)}
        for col in table.columns:
            if col.name in have:
                continue
            ddl_type = col.type.compile(dialect=bind.dialect)
            with bind.begin() as conn:
                conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {ddl_type}'))
            added.append(f"{table.name}.{col.name}")
    return added


def init_db() -> None:
    """Create tables if they are missing, and add any columns an older database lacks."""
    import models  # noqa: F401  (registers every table on Base.metadata)

    Base.metadata.create_all(engine)
    ensure_columns()
