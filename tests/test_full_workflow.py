"""
tests/test_full_workflow.py
───────────────────────────
Complete End-to-End Simulation of Spec v3.1 across all phases:
  Phase 1: Project Launch Detection (starts in PENDING_APPROVAL)
  Phase 2: Telegram Operator Approval (transitions to APPROVED)
  Phase 3: Official CA Announcement Received (transitions to CA_DETECTED)
  Phase 4: Pre-Buy Checks (2% Tax Gate, $100 Genuine Volume Snapshot, Honeypot Check)
  Phase 5 & 6: Rapid Fast-Path Buy Execution (< 2s) -> Order FILLED & Position OPENED (transitions to PURCHASED)
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from approval.approval_service import ApprovalService
from database.models import (
    Base,
    CheckResult,
    EventType,
    OrderStatus,
    PositionStatus,
    Project,
    ProjectStatus,
)
from monitoring.announcement_parser import AnnouncementParser, ParsedAnnouncement
from monitoring.ca_resolver import CAResolver
from monitoring.event_detector import EventDetector
from projects.registry import ProjectRegistry
from projects.status_lifecycle import assert_valid_transition
from security.contract_scanner import ContractSecurityScanner
from security.tax_checker import TaxChecker
from trading.emergency import EmergencyManager
from trading.order_manager import OrderManager
from trading.wallet_manager import WalletManager
from volume.pool_volume_tracker import PoolVolumeTracker
from volume.wash_trade_filters import TradeEvent


async def _setup_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    session = session_maker()
    return engine, session


def test_complete_end_to_end_sniper_workflow():
    async def _test():
        engine, session = await _setup_session()
        try:
            # ─────────────────────────────────────────────────────────────
            # PHASE 1: Launch Discovery on X
            # ─────────────────────────────────────────────────────────────
            detector = EventDetector()
            parser = AnnouncementParser()
            registry = ProjectRegistry(session)

            raw_launch_msg = {
                "platform": "x",
                "message_id": "tweet_1001",
                "post_url": "https://x.com/RocketDev/status/1001",
                "text": "Official announcement: $ROCKET token launching today on Robinhood Chain! Official X: @RocketDev",
                "author_id": "RocketDev",
                "published_at": "2026-11-04T12:00:00Z",
                "received_at": "2026-11-04T12:00:02Z",
                "is_forwarded": False,
            }

            event_type = detector.detect(raw_launch_msg)
            assert event_type == EventType.LAUNCH_INTENT

            parsed = parser.parse(raw_launch_msg, event_type)
            assert parsed.ticker == "ROCKET"
            assert parsed.chain_id == "robinhood"

            project, created = await registry.get_or_create_project(parsed)
            await session.commit()

            assert created is True
            assert project.status == ProjectStatus.PENDING_APPROVAL
            assert project.ticker == "ROCKET"

            # ─────────────────────────────────────────────────────────────
            # PHASE 2: Operator Reviews & Approves via Telegram
            # ─────────────────────────────────────────────────────────────
            approval_svc = ApprovalService(session)
            operator_id = "987654321"

            await approval_svc.approve(
                project_id=project.id,
                operator_id=operator_id,
                reason="Audited team, credible launch",
            )
            await session.commit()

            assert project.status == ProjectStatus.APPROVED

            # ─────────────────────────────────────────────────────────────
            # PHASE 3: Official CA Announcement Published on X
            # ─────────────────────────────────────────────────────────────
            ca_resolver = CAResolver(session)
            raw_ca_msg = {
                "platform": "x",
                "message_id": "tweet_1002",
                "text": "Contract Address is now LIVE: 0x9999999999999999999999999999999999999999",
                "sender_username": "RocketDev",
                "author_id": "RocketDev",
                "is_forwarded": False,
            }

            cand, ca_msg = await ca_resolver.process_ca_announcement(
                project=project,
                text=raw_ca_msg["text"],
                platform="x",
                sender_username="RocketDev",
            )
            await session.commit()

            assert cand is not None
            assert cand.has_conflict is False
            assert project.status == ProjectStatus.CA_DETECTED
            assert project.ca_address == "0x9999999999999999999999999999999999999999"

            # ─────────────────────────────────────────────────────────────
            # PHASE 4: Pre-Buy Checks (Tax, Volume Snapshot, Security)
            # ─────────────────────────────────────────────────────────────
            tax_checker = TaxChecker()
            # Buy tax 1.5% <= 2.0%, Sell tax 2.0% <= 2.0%
            tax_res = tax_checker.evaluate_taxes(sim_buy_tax=1.5, sim_sell_tax=2.0)
            assert tax_res.status == CheckResult.PASS

            # Volume Snapshot in primary pool ($150 genuine volume >= $100)
            volume_tracker = PoolVolumeTracker()
            now = datetime.now(timezone.utc)
            trades = [
                TradeEvent("txA", "0xTrader1", is_buy=True, amount_usd=100.0, timestamp=now),
                TradeEvent("txB", "0xTrader2", is_buy=False, amount_usd=50.0, timestamp=now),
            ]
            vol_res = volume_tracker.evaluate_volume_snapshot("0xPool", trades, now)
            assert vol_res.status == CheckResult.PASS
            assert vol_res.genuine_volume_usd == 150.0

            # Contract Security Scan
            sec_scanner = ContractSecurityScanner()
            sec_res = sec_scanner.evaluate_security(
                chain_id="robinhood",
                ca_address=project.ca_address,
                is_sellable=True,
                is_honeypot=False,
            )
            assert sec_res.status == CheckResult.PASS

            # All checks pass -> Transition to CHECKS_PASSED
            assert_valid_transition(project.status, ProjectStatus.CHECKS_PASSED, project.id)
            project.status = ProjectStatus.CHECKS_PASSED
            await session.commit()

            # ─────────────────────────────────────────────────────────────
            # PHASE 5 & 6: Rapid Fast-Path Buy Execution
            # ─────────────────────────────────────────────────────────────
            wm = WalletManager()
            em = EmergencyManager()
            om = OrderManager(session, wallet_mgr=wm, emergency_mgr=em)

            order, position, exec_msg = await om.execute_approved_buy(project, amount_usd=50.0)
            await session.commit()

            # Order must be FILLED
            assert order is not None
            assert order.status == OrderStatus.FILLED
            assert order.tx_hash.startswith("0x")
            assert order.amount_usd == 50.0

            # Position must be OPEN
            assert position is not None
            assert position.status == PositionStatus.OPEN
            assert position.token_address == project.ca_address
            assert position.cost_basis_usd == 50.0

            # Project must be in PURCHASED state
            assert project.status == ProjectStatus.PURCHASED

            # Wallet manager must reflect active open position & spent capital
            assert wm.open_positions_count == 1
            assert wm.daily_spent_usd == 50.0

        finally:
            await session.close()
            await engine.dispose()

    asyncio.run(_test())


def test_pre_buy_tax_failure_blocks_execution():
    """Verify that if tax exceeds 2.0%, project is blocked and no buy occurs."""
    async def _test():
        engine, session = await _setup_session()
        try:
            detector = EventDetector()
            parser = AnnouncementParser()
            registry = ProjectRegistry(session)

            raw_launch_msg = {
                "platform": "x",
                "message_id": "tweet_2001",
                "post_url": "https://x.com/TaxToken/status/2001",
                "text": "Launching $TAX token today on Robinhood Chain! Official X: @TaxToken",
                "author_id": "TaxToken",
                "published_at": "2026-11-04T12:00:00Z",
                "received_at": "2026-11-04T12:00:02Z",
                "is_forwarded": False,
            }
            event_type = detector.detect(raw_launch_msg)
            parsed = parser.parse(raw_launch_msg, event_type)
            project, _ = await registry.get_or_create_project(parsed)
            await session.commit()

            # Approve project
            approval_svc = ApprovalService(session)
            await approval_svc.approve(project.id, operator_id="111")
            await session.commit()
            assert project.status == ProjectStatus.APPROVED

            # Detect CA
            ca_resolver = CAResolver(session)
            cand, _ = await ca_resolver.process_ca_announcement(
                project=project,
                text="Official CA: 0x2222222222222222222222222222222222222222",
                platform="x",
                sender_username="TaxToken",
            )
            await session.commit()
            assert project.status == ProjectStatus.CA_DETECTED

            # Pre-buy tax check fails (4.5% > 2.0%)
            tax_checker = TaxChecker()
            tax_res = tax_checker.evaluate_taxes(sim_buy_tax=4.5, sim_sell_tax=1.0)
            assert tax_res.status == CheckResult.FAIL

            # Transition to SAFETY_BLOCKED
            assert_valid_transition(project.status, ProjectStatus.SAFETY_BLOCKED, project.id)
            project.status = ProjectStatus.SAFETY_BLOCKED
            await session.commit()

            # Attempt buy -> must be refused
            om = OrderManager(session)
            order, pos, msg = await om.execute_approved_buy(project)
            assert order is None
            assert pos is None
            assert "must be CHECKS_PASSED" in msg

        finally:
            await session.close()
            await engine.dispose()

    asyncio.run(_test())


def test_operator_rejection_blacklists_and_blocks():
    """Verify operator rejection transitions project to REJECTED, blacklists it, and blocks further events."""
    async def _test():
        engine, session = await _setup_session()
        try:
            detector = EventDetector()
            parser = AnnouncementParser()
            registry = ProjectRegistry(session)

            raw_launch_msg = {
                "platform": "x",
                "message_id": "tweet_3001",
                "post_url": "https://x.com/ScamDev/status/3001",
                "text": "Launching $SCAM on Robinhood Chain! Official X: @ScamDev",
                "author_id": "ScamDev",
                "published_at": "2026-11-04T12:00:00Z",
                "received_at": "2026-11-04T12:00:02Z",
                "is_forwarded": False,
            }
            event_type = detector.detect(raw_launch_msg)
            parsed = parser.parse(raw_launch_msg, event_type)
            project, _ = await registry.get_or_create_project(parsed)
            await session.commit()
            assert project.status == ProjectStatus.PENDING_APPROVAL

            # Operator rejects
            approval_svc = ApprovalService(session)
            await approval_svc.reject(project.id, operator_id="111", reason="Known scammer")
            await session.commit()
            assert project.status == ProjectStatus.REJECTED
            assert project.is_blacklisted is True

            # Matcher now reports blacklisted
            from projects.identity_matcher import IdentityMatcher
            matcher = IdentityMatcher(session)
            is_bl = await matcher.is_blacklisted(
                ticker="SCAM",
                chain_id="robinhood",
                official_x_handle="ScamDev",
                ca_address="0xScamTokenContractAddress",
            )
            assert is_bl is True

        finally:
            await session.close()
            await engine.dispose()

    asyncio.run(_test())


def test_emergency_circuit_breaker_halts_buy():
    """Verify that triggering an emergency circuit breaker blocks buy even if CHECKS_PASSED."""
    async def _test():
        engine, session = await _setup_session()
        try:
            project = Project(
                id="proj-circuit-breaker",
                name="SafeToken",
                ticker="SAFE",
                chain_id="robinhood",
                ca_address="0x3333333333333333333333333333333333333333",
                status=ProjectStatus.CHECKS_PASSED,
            )
            session.add(project)
            await session.commit()

            em = EmergencyManager()
            em.pause_chain("robinhood")

            om = OrderManager(session, emergency_mgr=em)
            order, pos, msg = await om.execute_approved_buy(project)

            assert order is None
            assert pos is None
            assert "paused for chain robinhood" in msg

            # Now resume chain and verify trade proceeds
            em.resume_chain("robinhood")
            order2, pos2, msg2 = await om.execute_approved_buy(project)
            assert order2 is not None
            assert order2.status == OrderStatus.FILLED
            assert project.status == ProjectStatus.PURCHASED

        finally:
            await session.close()
            await engine.dispose()

    asyncio.run(_test())
