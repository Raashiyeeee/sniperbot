"""
database/connection.py
──────────────────────
Async SQLAlchemy engine + session factory.
Supports PostgreSQL (with asyncpg) and graceful auto-fallback to SQLite for local development.
"""
from __future__ import annotations

import logging
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from config.settings import get_settings

logger = logging.getLogger(__name__)
_settings = get_settings()

db_url = _settings.database_url
engine_kwargs = {"echo": False}


def _make_sqlite_engine():
    return create_async_engine(
        "sqlite+aiosqlite:///radar_dev.db",
        **engine_kwargs,
    )


try:
    if "asyncpg" in db_url:
        import asyncpg  # noqa: F401
    engine = create_async_engine(
        db_url,
        **engine_kwargs,
    )
except (ImportError, Exception):
    engine = _make_sqlite_engine()

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


def _switch_to_sqlite() -> None:
    global engine, AsyncSessionLocal
    print("\n⚠️ PostgreSQL is not accessible on localhost:5432.")
    print("Switching seamlessly to local SQLite database: radar_dev.db\n")
    engine = _make_sqlite_engine()
    AsyncSessionLocal.configure(bind=engine)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields a database session."""
    try:
        async with AsyncSessionLocal() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise
            finally:
                await session.close()
    except Exception:
        if "sqlite" not in str(engine.url):
            _switch_to_sqlite()
            async with AsyncSessionLocal() as session:
                yield session
                await session.commit()
        else:
            raise


async def _ensure_sqlite_columns(conn) -> None:
    """Ensure newly added columns exist in existing SQLite databases."""
    from sqlalchemy import text
    try:
        for t, col, col_type in [
            ("projects", "expected_ca_time_str", "VARCHAR(256) DEFAULT 'Not specified'"),
            ("projects", "launch_time_str", "VARCHAR(256) DEFAULT 'Not specified'"),
            ("projects", "network", "VARCHAR(64)"),
            ("announcements", "expected_ca_time_str", "VARCHAR(256) DEFAULT 'Not specified'"),
            ("announcements", "launch_time_str", "VARCHAR(256) DEFAULT 'Not specified'"),
            ("announcements", "x_profile_url", "VARCHAR(1024)"),
            ("announcements", "network", "VARCHAR(64)"),
            ("source_accounts", "has_username_changes", "BOOLEAN DEFAULT 0"),
            ("source_accounts", "username_changes", "JSON DEFAULT '[]'"),
        ]:
            res = await conn.execute(text(f"PRAGMA table_info({t})"))
            cols = [r[1] for r in res.fetchall()]
            if col not in cols:
                await conn.execute(text(f"ALTER TABLE {t} ADD COLUMN {col} {col_type}"))
    except Exception:
        pass


async def create_tables() -> None:
    """Create all tables (used for dev; production uses Alembic migrations)."""
    from database.models import Base
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await _ensure_sqlite_columns(conn)
    except Exception:
        if "sqlite" not in str(engine.url):
            _switch_to_sqlite()
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
                await _ensure_sqlite_columns(conn)
        else:
            raise
