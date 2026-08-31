from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import MetaData, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import ROOT, settings


def application_schema(database_url: str) -> str | None:
    """Keep v4 tables isolated from the legacy per-save schemas.

    The hosted Neon URL uses a transaction pooler.  A pooled connection may
    retain a legacy ``search_path`` set by the 3.x application, so relying on
    PostgreSQL's implicit schema can create or query the v4 tables in whichever
    legacy save schema the pool returns.  Giving every v4 table an explicit
    public schema makes DDL and ORM queries deterministic.  SQLite has no
    equivalent schema and must continue to use its default namespace.
    """
    return None if database_url.startswith("sqlite") else "public"


class Base(DeclarativeBase):
    metadata = MetaData(schema=application_schema(settings.database_url))


if settings.database_url.startswith("sqlite"):
    relative = settings.database_url.removeprefix("sqlite:///")
    if not Path(relative).is_absolute():
        (ROOT / relative).parent.mkdir(parents=True, exist_ok=True)

engine_options = {"pool_pre_ping": True}
if settings.database_url.startswith("sqlite"):
    engine_options["connect_args"] = {"check_same_thread": False}
else:
    # Clock Sync can legitimately arrive alongside page loads and background
    # delivery work. Five total connections caused the hosted receiver to
    # reject ordinary bursts with QueuePool timeouts. Keep the pool bounded
    # for Railway/Neon, but leave enough headroom for those independent jobs.
    pool_size = max(2, int(os.getenv("DECADES_DB_POOL_SIZE", "8")))
    max_overflow = max(0, int(os.getenv("DECADES_DB_MAX_OVERFLOW", "4")))
    pool_timeout = max(5, int(os.getenv("DECADES_DB_POOL_TIMEOUT", "30")))
    engine_options.update(
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_timeout=pool_timeout,
        pool_recycle=300,
        pool_use_lifo=True,
    )

engine = create_engine(settings.sqlalchemy_database_url, **engine_options)


if settings.database_url.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(connection, _record):
        cursor = connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()


SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def session_scope() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
