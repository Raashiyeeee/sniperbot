"""
tests/test_ca_monitoring.py
───────────────────────────
Tests for Phase 2 & 3:
  - CA extraction (EVM & Solana)
  - Approved-only CA monitoring
  - Source authenticity (official X account, authorized Telegram admin, reject forwarded)
  - Conflicting CA detection & pausing
  - Duplicate protection
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from approval.approval_service import ApprovalService
from database.models import Base, Project, ProjectStatus, SourceAccount, Platform, SourceType
from monitoring.ca_extractor import CAExtractor
from monitoring.ca_resolver import CAResolver


async def _setup_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    session = session_maker()
    return engine, session


def test_ca_extractor_evm_and_solana():
    text_evm = "Check out the token at 0x1234567890abcdef1234567890abcdef12345678 on Robinhood"
    evm_cands = CAExtractor.extract_evm_candidates(text_evm)
    assert len(evm_cands) == 1
    assert evm_cands[0] == "0x1234567890abcdef1234567890abcdef12345678"

    text_sol = "Mint address: 4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R trading on Raydium"
    sol_cands = CAExtractor.extract_solana_candidates(text_sol)
    assert len(sol_cands) == 1
    assert sol_cands[0] == "4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R"


def test_unapproved_project_ignores_ca():
    async def _test():
        engine, session = await _setup_session()
        try:
            resolver = CAResolver(session)
            # Project is still in PENDING_APPROVAL
            proj = Project(
                id="proj-pending",
                name="PendingProject",
                ticker="PND",
                chain_id="robinhood",
                status=ProjectStatus.PENDING_APPROVAL,
            )
            session.add(proj)
            await session.commit()

            cand, msg = await resolver.process_ca_announcement(
                project=proj,
                text="Official CA: 0x1111111111111111111111111111111111111111",
                platform="x",
                sender_username="random_guy",
            )
            assert cand is None
            assert "must be APPROVED" in msg
        finally:
            await session.close()
            await engine.dispose()

    asyncio.run(_test())


def test_telegram_forwarded_message_rejected():
    async def _test():
        engine, session = await _setup_session()
        try:
            resolver = CAResolver(session)
            proj = Project(
                id="proj-approved-tg",
                name="ApprovedProject",
                ticker="APPR",
                chain_id="robinhood",
                status=ProjectStatus.APPROVED,
                official_telegram_chat_id="-100111",
            )
            session.add(proj)
            await session.commit()

            # Forwarded message from Telegram
            cand, msg = await resolver.process_ca_announcement(
                project=proj,
                text="Official CA: 0x1111111111111111111111111111111111111111",
                platform="telegram",
                sender_id="12345",
                is_forwarded=True,  # PROHIBITED
                chat_id="-100111",
            )
            assert cand is None
            assert "Forwarded messages are prohibited" in msg
        finally:
            await session.close()
            await engine.dispose()

    asyncio.run(_test())


def test_telegram_non_admin_rejected():
    async def _test():
        engine, session = await _setup_session()
        try:
            resolver = CAResolver(session)
            proj = Project(
                id="proj-tg-admin",
                name="AdminGate",
                ticker="ADMG",
                chain_id="robinhood",
                status=ProjectStatus.APPROVED,
                official_telegram="admingate",
                official_telegram_chat_id="-100999",
            )
            src = SourceAccount(
                id="src-tg-1",
                platform=Platform.TELEGRAM,
                account_id="-100999",
                handle="admingate",
                source_type=SourceType.OFFICIAL_PROJECT,
                is_active=True,
                trust_verified=True,
                authorized_admin_ids={"admins": ["111", "222"]},
            )
            session.add_all([proj, src])
            await session.commit()

            cand, msg = await resolver.process_ca_announcement(
                project=proj,
                text="CA: 0x1111111111111111111111111111111111111111",
                platform="telegram",
                sender_id="99999",  # ordinary member
                source_account_id=src.id,
                chat_id="-100999",
            )
            assert cand is None
            assert "not an authorized administrator" in msg
        finally:
            await session.close()
            await engine.dispose()

    asyncio.run(_test())


def test_telegram_admin_ca_accepted():
    async def _test():
        engine, session = await _setup_session()
        try:
            resolver = CAResolver(session)
            proj = Project(
                id="proj-tg-ok",
                name="AdminOK",
                ticker="ADOK",
                chain_id="robinhood",
                status=ProjectStatus.APPROVED,
                official_telegram="adminok",
                official_telegram_chat_id="-100888",
            )
            src = SourceAccount(
                id="src-tg-2",
                platform=Platform.TELEGRAM,
                account_id="-100888",
                handle="adminok",
                source_type=SourceType.OFFICIAL_PROJECT,
                is_active=True,
                trust_verified=True,
                authorized_admin_ids={"admins": ["4242"]},
            )
            session.add_all([proj, src])
            await session.commit()

            cand, msg = await resolver.process_ca_announcement(
                project=proj,
                text="Official CA: 0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                platform="telegram",
                sender_id="4242",
                source_account_id=src.id,
                chat_id="-100888",
            )
            assert cand is not None
            assert cand.has_conflict is False
            assert proj.status == ProjectStatus.CA_DETECTED
            assert "verified" in msg.lower() or cand.is_verified
        finally:
            await session.close()
            await engine.dispose()

    asyncio.run(_test())


def test_telegram_wrong_chat_rejected():
    async def _test():
        engine, session = await _setup_session()
        try:
            resolver = CAResolver(session)
            proj = Project(
                id="proj-tg-chat",
                name="ChatScope",
                ticker="CHSC",
                chain_id="robinhood",
                status=ProjectStatus.APPROVED,
                official_telegram_chat_id="-100777",
            )
            src = SourceAccount(
                id="src-tg-3",
                platform=Platform.TELEGRAM,
                account_id="-100777",
                handle="chatscope",
                source_type=SourceType.OFFICIAL_PROJECT,
                is_active=True,
                trust_verified=True,
                authorized_admin_ids={"admins": ["55"]},
            )
            session.add_all([proj, src])
            await session.commit()

            cand, msg = await resolver.process_ca_announcement(
                project=proj,
                text="CA: 0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                platform="telegram",
                sender_id="55",
                source_account_id=src.id,
                chat_id="-100000",  # wrong chat
            )
            assert cand is None
            assert "chat mismatch" in msg.lower()
        finally:
            await session.close()
            await engine.dispose()

    asyncio.run(_test())


def test_conflicting_ca_detection():
    async def _test():
        engine, session = await _setup_session()
        try:
            resolver = CAResolver(session)
            proj = Project(
                id="proj-conflict",
                name="ConflictToken",
                ticker="CONF",
                chain_id="robinhood",
                status=ProjectStatus.APPROVED,
                official_x_handle="conflictofficial",
            )
            session.add(proj)
            await session.commit()

            # 1. First CA published
            cand1, msg1 = await resolver.process_ca_announcement(
                project=proj,
                text="CA: 0x1111111111111111111111111111111111111111",
                platform="x",
                sender_username="conflictofficial",
            )
            assert cand1 is not None
            assert proj.status == ProjectStatus.CA_DETECTED
            await session.commit()

            # 2. Conflicting different CA published!
            cand2, msg2 = await resolver.process_ca_announcement(
                project=proj,
                text="Correction: 0x2222222222222222222222222222222222222222",
                platform="x",
                sender_username="conflictofficial",
            )
            # Workflow must pause and transition to SAFETY_BLOCKED
            assert cand2.has_conflict is True
            assert proj.status == ProjectStatus.SAFETY_BLOCKED
            assert "Conflicting CA detected" in cand2.conflict_note
        finally:
            await session.close()
            await engine.dispose()

    asyncio.run(_test())


def test_approval_and_rejection_workflow():
    async def _test():
        engine, session = await _setup_session()
        try:
            appr_svc = ApprovalService(session)
            proj = Project(
                id="proj-review",
                name="ReviewProject",
                ticker="REV",
                chain_id="robinhood",
                status=ProjectStatus.PENDING_APPROVAL,
            )
            session.add(proj)
            await session.commit()

            # Operator approves
            await appr_svc.approve(proj.id, operator_id="12345", reason="Good roadmap")
            await session.commit()
            assert proj.status == ProjectStatus.APPROVED

            # Operator later rejects another project -> blacklisted
            proj2 = Project(
                id="proj-scam",
                name="ScamProject",
                ticker="SCAM",
                chain_id="solana",
                status=ProjectStatus.PENDING_APPROVAL,
            )
            session.add(proj2)
            await session.commit()

            await appr_svc.reject(proj2.id, operator_id="12345", reason="Fake founder")
            await session.commit()
            assert proj2.status == ProjectStatus.REJECTED
            assert proj2.is_blacklisted is True

            # Unblock project
            await appr_svc.unblock(proj2.id, operator_id="12345", reason="Verified real founder")
            await session.commit()
            assert proj2.status == ProjectStatus.PENDING_APPROVAL
            assert proj2.is_blacklisted is False
        finally:
            await session.close()
            await engine.dispose()

    asyncio.run(_test())
