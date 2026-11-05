"""
tests/test_two_tier_pipeline.py
───────────────────────────────
Unit tests for the Two-Tier Architecture:
1. Global Discovery Filter: Only alerts Telegram for new projects containing high-intent keywords
   ("CA soon", "contract dropping", "contract dropping soon").
   General promos, memes, and ordinary launches are dropped (no unwanted spam notifications).
2. Approved Projects: Tracks approved projects directly and triggers fast-path buy on CA drop.
"""
from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from database.models import Base, EventType, Platform, Project, ProjectStatus
from main import DISCOVERY_KEYWORDS_RE, process_message
from monitoring.announcement_parser import AnnouncementParser
from monitoring.event_detector import EventDetector
from telegram_bot.alerts import AlertService


@pytest.fixture
def detector() -> EventDetector:
    return EventDetector()


@pytest.fixture
def parser() -> AnnouncementParser:
    return AnnouncementParser()


def test_discovery_keywords_regex():
    # Must match high-intent CA keywords requiring 'soon'
    assert DISCOVERY_KEYWORDS_RE.search("Huge launch! CA soon!")
    assert DISCOVERY_KEYWORDS_RE.search("Contract dropping soon!")
    assert DISCOVERY_KEYWORDS_RE.search("Contract address dropping soon for $SOL")
    assert DISCOVERY_KEYWORDS_RE.search("Dropping CA soon")

    # Must NOT match bare 'contract dropping' without 'soon' (Image 1 fix)
    assert not DISCOVERY_KEYWORDS_RE.search("Turning notifications on contract dropping")
    assert not DISCOVERY_KEYWORDS_RE.search("contract dropping")

    # Must NOT match general launch noise (prevents spam alerts across X)
    assert not DISCOVERY_KEYWORDS_RE.search("Fair launch today! Buy on raydium")
    assert not DISCOVERY_KEYWORDS_RE.search("Stealth launch 1000x on solana pump.fun")
    assert not DISCOVERY_KEYWORDS_RE.search("Meme coin launching tomorrow")
    assert not DISCOVERY_KEYWORDS_RE.search("Join our telegram for more updates")


def test_official_page_filter():
    from security.official_page_filter import OfficialPageFilter
    f = OfficialPageFilter()

    # Image 1: Bystander shill (@Shilllin with "Turning notifications on...")
    is_off, reason = f.evaluate(
        text="Green candle army is aligning with the timeline cabal Turning notifications on contract dropping soon",
        author_handle="Shilllin",
    )
    assert is_off is False
    assert reason is not None
    r_lower = reason.lower()
    assert "turning notifications on" in r_lower or "shill" in r_lower

    # Genuine official project page
    is_off, reason = f.evaluate(
        text="Official announcement: $ROCKET token CA soon! Contract address dropping soon.",
        author_handle="RocketProtocol",
    )
    assert is_off is True
    assert reason is None


@pytest.mark.anyio
async def test_unapproved_general_promo_dropped_without_alert(detector, parser):
    """Test that general promo without 'CA soon' or 'contract dropping' sends NO alert."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    mock_alert_service = MagicMock(spec=AlertService)
    mock_alert_service.send_launch_detected = AsyncMock()

    msg = {
        "platform": "x",
        "message_id": "1111",
        "text": "Fair launch today for $MEME on Solana! Don't miss out!",
        "author_id": "meme_dev",
        "published_at": "2026-11-05T12:00:00Z",
        "received_at": "2026-11-05T12:00:00Z",
    }

    with patch("main.AsyncSessionLocal", session_maker):
        await process_message(msg, detector, parser, mock_alert_service)

    # Should NOT have alerted on Telegram!
    assert not mock_alert_service.send_launch_detected.called

    await engine.dispose()


@pytest.mark.anyio
async def test_unapproved_ca_soon_sends_telegram_alert(detector, parser):
    """Test that posts with 'CA soon' or 'contract dropping' trigger Telegram alert."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    mock_alert_service = MagicMock(spec=AlertService)
    mock_alert_service.send_launch_detected = AsyncMock()

    msg = {
        "platform": "x",
        "message_id": "2222",
        "text": "Official launch of $ALPHA token! CA soon! Stay tuned.",
        "author_id": "alpha_official",
        "published_at": "2026-11-05T12:00:00Z",
        "received_at": "2026-11-05T12:00:00Z",
    }

    with patch("main.AsyncSessionLocal", session_maker):
        await process_message(msg, detector, parser, mock_alert_service)

    # Must have alerted operator on Telegram with [✓ Accept] / [✕ Reject]!
    assert mock_alert_service.send_launch_detected.called
    project_arg = mock_alert_service.send_launch_detected.call_args[0][0]
    assert project_arg.status == ProjectStatus.PENDING_APPROVAL

    await engine.dispose()


@pytest.mark.anyio
async def test_approved_project_updates_and_triggers_fast_path(detector, parser):
    """Test that once approved, the bot monitors tweets and triggers fast-path buy on CA drop."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    # Create pre-existing APPROVED project
    async with session_maker() as s:
        proj = Project(
            id=str(uuid.uuid4()),
            name="AlphaToken",
            ticker="ALPHA",
            chain_id="solana",
            official_x_handle="alphadev",
            status=ProjectStatus.APPROVED,
        )
        s.add(proj)
        await s.commit()
        proj_id = proj.id

    mock_alert_service = MagicMock(spec=AlertService)
    mock_alert_service.send_launch_detected = AsyncMock()
    mock_alert_service.send_ca_detected = AsyncMock()

    # Tweet 1 from approved project: Countdown update (doesn't have CA soon, but it's APPROVED)
    msg_update = {
        "platform": "x",
        "message_id": "3333",
        "text": "Token launch at 5 PM UTC tomorrow. CA will be revealed in 2 hours.",
        "author_id": "alphadev",
        "published_at": "2026-11-05T12:00:00Z",
        "received_at": "2026-11-05T12:00:00Z",
    }

    with patch("main.AsyncSessionLocal", session_maker):
        await process_message(msg_update, detector, parser, mock_alert_service)

    # Check project details were updated
    async with session_maker() as s:
        p = await s.get(Project, proj_id)
        assert p.expected_ca_time_str == "In approximately 2 hours"
        assert "5:00 PM UTC" in p.launch_time_str

    # Tweet 2 from approved project: CA Drop!
    msg_ca = {
        "platform": "x",
        "message_id": "4444",
        "text": "Official CA is live: 4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R",
        "author_id": "alphadev",
        "published_at": "2026-11-05T14:00:00Z",
        "received_at": "2026-11-05T14:00:00Z",
    }

    with patch("main.AsyncSessionLocal", session_maker):
        await process_message(msg_ca, detector, parser, mock_alert_service)

    # Verify CA was detected and alerted for approved project
    assert mock_alert_service.send_ca_detected.called

    await engine.dispose()

