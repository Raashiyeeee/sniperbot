"""
tests/test_account_manager.py
─────────────────────────────
Official account attachment helpers + approved_sources registry.
"""
from __future__ import annotations

import asyncio

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from database.models import Base, Project, ProjectStatus
from monitoring.approved_sources import ApprovedSourcesRegistry
from projects.account_manager import AccountManager, normalize_x_handle, parse_telegram_ref


def test_normalize_x_handle():
    assert normalize_x_handle("@FooBar") == "foobar"
    assert normalize_x_handle("  Baz  ") == "baz"


def test_parse_telegram_ref():
    assert parse_telegram_ref("-1001234567890") == ("-1001234567890", None)
    assert parse_telegram_ref("@MyChannel") == (None, "mychannel")
    assert parse_telegram_ref("https://t.me/MyChannel") == (None, "mychannel")


def test_approved_sources_registry():
    reg = ApprovedSourcesRegistry()
    reg.set_telegram_chat("-1001", "proj-a")
    reg.set_x_handle("@Alpha", "proj-a")
    assert reg.is_allowed_telegram_chat("-1001")
    assert not reg.is_allowed_telegram_chat("-1002")
    assert "alpha" in reg.allowed_x_handles()
    assert reg.project_id_for_telegram_chat("-1001") == "proj-a"


def test_account_manager_sets_x_and_registers():
    async def _test():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with session_maker() as session:
            proj = Project(
                id="proj-acct-1",
                name="AcctTest",
                ticker="ACCT",
                chain_id="robinhood",
                status=ProjectStatus.APPROVED,
            )
            session.add(proj)
            await session.commit()

            # Isolate registry for this test
            from monitoring import approved_sources as as_mod

            as_mod.approved_sources.replace_all({}, {})

            mgr = AccountManager(session, bot=None)
            await mgr.set_x_account(proj, "@OfficialAcct", operator_id="1")
            await session.commit()

            assert proj.official_x_handle == "officialacct"
            assert "officialacct" in as_mod.approved_sources.allowed_x_handles()

            await mgr.set_telegram_account(proj, "-100555", operator_id="1")
            await session.commit()
            assert proj.official_telegram_chat_id == "-100555"
            assert as_mod.approved_sources.is_allowed_telegram_chat("-100555")

        await engine.dispose()

    asyncio.run(_test())
