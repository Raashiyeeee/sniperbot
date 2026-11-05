"""
tests/test_registry.py
───────────────────────
Integration tests for ProjectRegistry with an in-memory SQLite database.
Tests deduplication, project registration, status updates, and audit logging.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from database.models import Base, EventType, Project, ProjectStatus
from monitoring.announcement_parser import ParsedAnnouncement
from projects.registry import ProjectRegistry


def run_async(coro):
    return asyncio.run(coro)


async def _setup_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    session = session_maker()
    return engine, session


def test_create_new_project():
    async def _test():
        engine, session = await _setup_session()
        try:
            registry = ProjectRegistry(session)
            parsed = ParsedAnnouncement(
                platform="x",
                message_id="tx_101",
                post_url="https://x.com/post/101",
                text="Launching $ALPHA on Robinhood Chain!",
                author_id="AlphaTeam",
                event_type=EventType.LAUNCH_INTENT,
                published_at=datetime.now(timezone.utc),
                received_at=datetime.now(timezone.utc),
                is_forwarded=False,
                project_name="Alpha",
                ticker="ALPHA",
                chain_id="robinhood",
            )
            project, created = await registry.get_or_create_project(parsed)
            await session.commit()

            assert created is True
            assert project.id is not None
            assert project.ticker == "ALPHA"
            assert project.chain_id == "robinhood"
            assert project.status == ProjectStatus.PENDING_APPROVAL
        finally:
            await session.close()
            await engine.dispose()

    run_async(_test())


def test_deduplicate_by_ticker_and_chain():
    async def _test():
        engine, session = await _setup_session()
        try:
            registry = ProjectRegistry(session)

            # First announcement on X
            parsed_x = ParsedAnnouncement(
                platform="x",
                message_id="tx_201",
                post_url="https://x.com/post/201",
                text="Launching $BETA on Robinhood Chain!",
                author_id="BetaX",
                event_type=EventType.LAUNCH_INTENT,
                published_at=datetime.now(timezone.utc),
                received_at=datetime.now(timezone.utc),
                is_forwarded=False,
                project_name="Beta",
                ticker="BETA",
                chain_id="robinhood",
            )
            p1, created1 = await registry.get_or_create_project(parsed_x)
            await session.commit()
            assert created1 is True

            # Second announcement on Telegram with same ticker + chain
            parsed_tg = ParsedAnnouncement(
                platform="telegram",
                message_id="tg_202",
                post_url=None,
                text="Official TG for $BETA on Robinhood Chain",
                author_id="admin_beta",
                event_type=EventType.LAUNCH_INTENT,
                published_at=datetime.now(timezone.utc),
                received_at=datetime.now(timezone.utc),
                is_forwarded=False,
                project_name="Beta Token",
                ticker="BETA",
                chain_id="robinhood",
            )
            p2, created2 = await registry.get_or_create_project(parsed_tg)
            await session.commit()

            assert created2 is False
            assert p2.id == p1.id  # Same project identity!
        finally:
            await session.close()
            await engine.dispose()

    run_async(_test())


def test_status_update_writes_audit_log():
    async def _test():
        engine, session = await _setup_session()
        try:
            registry = ProjectRegistry(session)

            parsed = ParsedAnnouncement(
                platform="x",
                message_id="tx_301",
                post_url="https://x.com/post/301",
                text="Launching $GAMMA on Solana!",
                author_id="GammaSol",
                event_type=EventType.LAUNCH_INTENT,
                published_at=datetime.now(timezone.utc),
                received_at=datetime.now(timezone.utc),
                is_forwarded=False,
                ticker="GAMMA",
                chain_id="solana",
            )
            project, _ = await registry.get_or_create_project(parsed)
            await session.commit()

            # Operator approves project
            updated = await registry.update_project_status(
                project_id=project.id,
                new_status=ProjectStatus.APPROVED,
                actor="operator:123456",
                reason="Looks legit",
            )
            await session.commit()

            assert updated.status == ProjectStatus.APPROVED
        finally:
            await session.close()
            await engine.dispose()

    run_async(_test())

