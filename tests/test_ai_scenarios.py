"""
tests/test_ai_scenarios.py
──────────────────────────
Comprehensive test suite verifying all 20 required scenarios from
Master Implementation Prompt §16:

1. Genuine Web3 gaming project with an official TGE announcement.
2. Legitimate DeFi project with a working application.
3. Project with good social metrics but no actual utility.
4. Newly created fake X account impersonating an established project.
5. Ordinary product update with no token launch.
6. Random contract-address post.
7. Unofficial influencer announcing a token launch.
8. Official project announcing CA soon.
9. Official project publishing its contract address.
10. Duplicate announcement.
11. Gemini API unavailable.
12. Gemini returns malformed JSON.
13. Telegram approval is clicked twice.
14. Rejected project publishes another announcement.
15. Approved project publishes an unrelated contract address.
16. Solana contract fails validation.
17. Robinhood Chain contract fails validation.
18. Trading conditions are not satisfied.
19. RPC timeout.
20. Successful dry-run transaction.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ai.cache import EvaluationCacheManager
from ai.gemini_client import GeminiAPIError, GeminiClient
from ai.project_evaluator import ProjectEvaluator
from ai.schemas import (
    BlockchainType,
    ProjectCategory,
    ProjectIntelligenceResult,
    RecommendationLevel,
    VerificationStatus,
)
from config.chains import is_valid_evm_address, is_valid_solana_address
from config.settings import get_settings
from database.models import (
    Announcement,
    Approval,
    ApprovalDecision,
    Assessment,
    Base,
    BlacklistEntry,
    CheckResult,
    EventType,
    Order,
    OrderStatus,
    Position,
    PositionStatus,
    Project,
    ProjectStatus,
)
from monitoring.announcement_parser import AnnouncementParser, ParsedAnnouncement
from monitoring.ca_resolver import CAResolver
from monitoring.event_detector import EventDetector
from projects.identity_matcher import IdentityMatcher
from projects.registry import ProjectRegistry
from projects.status_lifecycle import assert_valid_transition, is_valid_transition
from security.contract_scanner import ContractSecurityScanner
from security.official_page_filter import OfficialPageFilter
from security.tax_checker import TaxChecker
from telegram_bot.alerts import AlertService
from telegram_bot.bot import TelegramBot
from trading.emergency import EmergencyManager
from trading.order_manager import OrderManager
from trading.quote_engine import Quote
from trading.wallet_manager import WalletManager
from volume.pool_volume_tracker import PoolVolumeTracker
from volume.wash_trade_filters import TradeEvent


async def _setup_session():
    """Create in-memory SQLite database session for tests."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    session = session_maker()
    return engine, session


# ══════════════════════════════════════════════════════════════════
# Scenario 1: Genuine Web3 gaming project with official TGE announcement
# ══════════════════════════════════════════════════════════════════
def test_scenario_01_gaming_project_tge():
    async def _test():
        engine, session = await _setup_session()
        try:
            mock_gemini = MagicMock(spec=GeminiClient)
            mock_gemini.is_configured = True
            mock_gemini.model = "gemini-flash-lite-latest"
            mock_gemini.generate_structured_json = AsyncMock(return_value={
                "project": {
                    "name": "Mythic Games",
                    "ticker": "MYTHIC",
                    "primary_category": "Gaming",
                    "categories": ["Gaming"],
                    "blockchain": "Solana",
                    "website_url": "https://mythicgames.io",
                    "official_x_handle": "@MythicGamesStudio",
                    "founder_team": "Mythic Team",
                    "product_summary": "Web3 battle RPG game on Solana.",
                    "documentation_url": "https://docs.mythicgames.io",
                },
                "product_analysis": {
                    "has_real_product": True,
                    "has_functional_website": True,
                    "has_documentation": True,
                    "utility_score": 90.0,
                    "verified_evidence": ["Playable demo live on mainnet"],
                    "unknowns": [],
                    "red_flags": [],
                },
                "credibility_analysis": {
                    "official_source_confirmed": True,
                    "team_identifiable": True,
                    "website_verified": True,
                    "ecosystem_presence": True,
                    "credibility_score": 85.0,
                    "ecosystem_score": 85.0,
                    "evidence": ["Solana grant recipient"],
                    "red_flags": [],
                },
                "social_analysis": {
                    "account_credibility_score": 85.0,
                    "community_quality_score": 85.0,
                    "account_age_assessment": "Established 2022",
                    "organic_engagement_detected": True,
                    "bot_activity_suspected": False,
                    "username_change_risk": False,
                    "red_flags": [],
                },
                "announcement_analysis": {
                    "announcement_type": "UPCOMING_TGE",
                    "is_official_announcement": True,
                    "is_authorized_source": True,
                    "launch_confirmed": True,
                    "confirmation_score": 90.0,
                    "announcement_url": "https://x.com/MythicGamesStudio/status/1",
                    "launch_date_str": "Tomorrow 2 PM UTC",
                    "expected_ca_time_str": "Tomorrow 2 PM UTC",
                },
                "verification": {
                    "official_website": "Verified",
                    "product_existence": "Verified",
                    "official_x_account": "Verified",
                    "team_association": "Verified",
                    "community_activity": "Verified",
                    "token_launch_announcement": "Verified",
                },
                "quality_assessment": {
                    "overall_score": 88.0,
                    "risk_level": "LOW",
                    "recommendation": "HIGH_QUALITY",
                    "mandatory_rejection": False,
                    "mandatory_rejection_reason": None,
                    "red_flags": [],
                    "missing_information": [],
                    "reasoning_summary": "Legitimate Web3 gaming studio with working product and announced TGE.",
                },
            })
            evaluator = ProjectEvaluator(gemini_client=mock_gemini)
            post_text = "We are thrilled to announce the official TGE and CA soon for $MYTHIC! Our Web3 battle RPG game is launching on Solana tomorrow at 2 PM UTC."
            result = await evaluator.evaluate_project(
                post_text=post_text,
                author_handle="MythicGamesStudio",
                author_name="Mythic Games",
                extracted_name="Mythic Games",
                extracted_ticker="MYTHIC",
                session=session,
            )
            assert result.project.primary_category == ProjectCategory.GAMING
            assert result.announcement_analysis.launch_confirmed is True
            assert result.quality_assessment.overall_score >= 80.0
            assert result.quality_assessment.recommendation == RecommendationLevel.HIGH_QUALITY
            assert not result.quality_assessment.mandatory_rejection
        finally:
            await session.close()
            await engine.dispose()

    asyncio.run(_test())


# ══════════════════════════════════════════════════════════════════
# Scenario 2: Legitimate DeFi project with a working application
# ══════════════════════════════════════════════════════════════════
def test_scenario_02_defi_project_working_app():
    async def _test():
        engine, session = await _setup_session()
        try:
            mock_gemini = MagicMock(spec=GeminiClient)
            mock_gemini.is_configured = True
            mock_gemini.model = "gemini-flash-lite-latest"
            mock_gemini.generate_structured_json = AsyncMock(return_value={
                "project": {
                    "name": "Lend Protocol",
                    "ticker": "LEND",
                    "primary_category": "DeFi",
                    "categories": ["DeFi"],
                    "blockchain": "Solana",
                    "website_url": "https://lendprotocol.finance",
                    "official_x_handle": "@LendProtocolOfficial",
                    "founder_team": "Lend Finance Team",
                    "product_summary": "Lending and borrowing protocol with $50M DEX volume.",
                    "documentation_url": "https://docs.lendprotocol.finance",
                },
                "product_analysis": {
                    "has_real_product": True,
                    "has_functional_website": True,
                    "has_documentation": True,
                    "utility_score": 92.0,
                    "verified_evidence": ["$50M DEX TVL verified via on-chain lending pools"],
                    "unknowns": [],
                    "red_flags": [],
                },
                "credibility_analysis": {
                    "official_source_confirmed": True,
                    "team_identifiable": True,
                    "website_verified": True,
                    "ecosystem_presence": True,
                    "credibility_score": 88.0,
                    "ecosystem_score": 85.0,
                    "evidence": ["Verified DeFi protocol"],
                    "red_flags": [],
                },
                "social_analysis": {
                    "account_credibility_score": 85.0,
                    "community_quality_score": 80.0,
                    "account_age_assessment": "Established account",
                    "organic_engagement_detected": True,
                    "bot_activity_suspected": False,
                    "username_change_risk": False,
                    "red_flags": [],
                },
                "announcement_analysis": {
                    "announcement_type": "UPCOMING_TGE",
                    "is_official_announcement": True,
                    "is_authorized_source": True,
                    "launch_confirmed": True,
                    "confirmation_score": 90.0,
                    "announcement_url": "https://x.com/LendProtocolOfficial/status/1",
                    "launch_date_str": "Confirmed soon",
                    "expected_ca_time_str": "Confirmed soon",
                },
                "verification": {
                    "official_website": "Verified",
                    "product_existence": "Verified",
                    "official_x_account": "Verified",
                    "team_association": "Verified",
                    "community_activity": "Verified",
                    "token_launch_announcement": "Verified",
                },
                "quality_assessment": {
                    "overall_score": 89.0,
                    "risk_level": "LOW",
                    "recommendation": "HIGH_QUALITY",
                    "mandatory_rejection": False,
                    "mandatory_rejection_reason": None,
                    "red_flags": [],
                    "missing_information": [],
                    "reasoning_summary": "Legitimate DeFi lending protocol with active volume.",
                },
            })
            evaluator = ProjectEvaluator(gemini_client=mock_gemini)
            post_text = "After $50M in DEX volume on our lending protocol, native governance token $LEND TGE confirmed! CA dropping soon on Solana."
            result = await evaluator.evaluate_project(
                post_text=post_text,
                author_handle="LendProtocolOfficial",
                author_name="Lend Protocol",
                extracted_name="Lend Protocol",
                extracted_ticker="LEND",
                session=session,
            )
            assert result.project.primary_category == ProjectCategory.DEFI
            assert result.product_analysis.has_real_product is True
            assert result.quality_assessment.overall_score >= 80.0
            assert result.announcement_analysis.launch_confirmed is True
        finally:
            await session.close()
            await engine.dispose()

    asyncio.run(_test())


# ══════════════════════════════════════════════════════════════════
# Scenario 3: Project with good social metrics but no actual utility
# ══════════════════════════════════════════════════════════════════
def test_scenario_03_social_hype_no_utility():
    async def _test():
        evaluator = ProjectEvaluator()
        mock_gemini = MagicMock(spec=GeminiClient)
        mock_gemini.is_configured = True
        mock_gemini.generate_structured_json = AsyncMock(return_value={
            "project": {
                "name": "HypeMeme",
                "ticker": "HYPE",
                "primary_category": "Other",
                "categories": ["Other"],
                "blockchain": "Solana",
                "website_url": None,
                "official_x_handle": "@HypeMeme",
                "official_telegram": None,
                "founder_team": None,
                "product_summary": "No product, just meme hype",
                "documentation_url": None
            },
            "product_analysis": {
                "has_real_product": False,
                "has_functional_website": False,
                "has_documentation": False,
                "utility_score": 10.0,
                "verified_evidence": [],
                "unknowns": ["No working product", "No documentation"],
                "red_flags": ["Zero utility detected"]
            },
            "credibility_analysis": {
                "official_source_confirmed": True,
                "team_identifiable": False,
                "website_verified": False,
                "ecosystem_presence": False,
                "credibility_score": 30.0,
                "ecosystem_score": 20.0,
                "evidence": [],
                "red_flags": []
            },
            "social_analysis": {
                "account_credibility_score": 80.0,
                "community_quality_score": 75.0,
                "account_age_assessment": "Active account",
                "organic_engagement_detected": True,
                "bot_activity_suspected": False,
                "username_change_risk": False,
                "red_flags": []
            },
            "announcement_analysis": {
                "announcement_type": "CA_SOON",
                "is_official_announcement": True,
                "is_authorized_source": True,
                "launch_confirmed": True,
                "confirmation_score": 80.0,
                "announcement_url": None,
                "launch_date_str": None,
                "expected_ca_time_str": "soon"
            },
            "verification": {
                "official_website": "Unknown",
                "product_existence": "Failed",
                "official_x_account": "Verified",
                "team_association": "Unknown",
                "community_activity": "Verified",
                "token_launch_announcement": "Verified"
            },
            "quality_assessment": {
                "overall_score": 45.0,
                "risk_level": "HIGH",
                "recommendation": "REJECT",
                "mandatory_rejection": False,
                "mandatory_rejection_reason": None,
                "red_flags": ["Zero utility detected"],
                "missing_information": ["Missing real product"],
                "reasoning_summary": "High social engagement but lacking utility"
            }
        })

        evaluator.gemini = mock_gemini
        result = await evaluator.evaluate_project(
            post_text="Huge viral token CA soon! Follow and RT!",
            author_handle="HypeMeme",
            session=None,
        )
        assert result.product_analysis.utility_score <= 20.0
        assert result.quality_assessment.overall_score < 60.0
        assert result.quality_assessment.recommendation == RecommendationLevel.REJECT

    asyncio.run(_test())


# ══════════════════════════════════════════════════════════════════
# Scenario 4: Newly created fake X account impersonating established project
# ══════════════════════════════════════════════════════════════════
def test_scenario_04_impersonation_detection():
    async def _test():
        evaluator = ProjectEvaluator()
        mock_gemini = MagicMock(spec=GeminiClient)
        mock_gemini.is_configured = True
        mock_gemini.generate_structured_json = AsyncMock(return_value={
            "project": {
                "name": "Uniswap Impersonator",
                "ticker": "UNI",
                "primary_category": "Trading",
                "categories": ["Trading"],
                "blockchain": "Solana",
                "website_url": "http://uniswap-fake-drop.xyz",
                "official_x_handle": "@unisvvap_sol",
                "official_telegram": None,
                "founder_team": None,
                "product_summary": None,
                "documentation_url": None
            },
            "product_analysis": {
                "has_real_product": False,
                "has_functional_website": False,
                "has_documentation": False,
                "utility_score": 0.0,
                "verified_evidence": [],
                "unknowns": [],
                "red_flags": ["Phishing domain"]
            },
            "credibility_analysis": {
                "official_source_confirmed": False,
                "team_identifiable": False,
                "website_verified": False,
                "ecosystem_presence": False,
                "credibility_score": 0.0,
                "ecosystem_score": 0.0,
                "evidence": [],
                "red_flags": ["Impersonating Uniswap"]
            },
            "social_analysis": {
                "account_credibility_score": 5.0,
                "community_quality_score": 5.0,
                "account_age_assessment": "Created today",
                "organic_engagement_detected": False,
                "bot_activity_suspected": True,
                "username_change_risk": True,
                "red_flags": ["Brand impersonation"]
            },
            "announcement_analysis": {
                "announcement_type": "UNKNOWN",
                "is_official_announcement": False,
                "is_authorized_source": False,
                "launch_confirmed": False,
                "confirmation_score": 0.0,
                "announcement_url": None,
                "launch_date_str": None,
                "expected_ca_time_str": None
            },
            "verification": {
                "official_website": "Failed",
                "product_existence": "Failed",
                "official_x_account": "Failed",
                "team_association": "Failed",
                "community_activity": "Failed",
                "token_launch_announcement": "Failed"
            },
            "quality_assessment": {
                "overall_score": 0.0,
                "risk_level": "HIGH",
                "recommendation": "REJECT",
                "mandatory_rejection": True,
                "mandatory_rejection_reason": "Confirmed impersonation of established protocol",
                "red_flags": ["Impersonation", "Phishing"],
                "missing_information": [],
                "reasoning_summary": "Scam account impersonating brand"
            }
        })
        evaluator.gemini = mock_gemini
        result = await evaluator.evaluate_project(
            post_text="Uniswap on Solana dropping CA soon! Connect wallet on uniswap-fake-drop.xyz",
            author_handle="unisvvap_sol",
        )
        assert result.quality_assessment.mandatory_rejection is True
        reason = result.quality_assessment.mandatory_rejection_reason or ""
        assert "impersonation" in reason.lower()
        assert result.quality_assessment.recommendation == RecommendationLevel.REJECT

    asyncio.run(_test())


# ══════════════════════════════════════════════════════════════════
# Scenario 5: Ordinary product update with no token launch
# ══════════════════════════════════════════════════════════════════
def test_scenario_05_ordinary_product_update_no_launch():
    detector = EventDetector()
    msg = {
        "text": "Version 2.4 is released with faster loading times and UI improvements! Check out our new dark mode.",
        "platform": "x",
    }
    event_type = detector.detect(msg)
    # Must NOT be classified as LAUNCH_INTENT or CA_ANNOUNCEMENT
    assert event_type not in (EventType.LAUNCH_INTENT, EventType.CA_ANNOUNCEMENT)


# ══════════════════════════════════════════════════════════════════
# Scenario 6: Random contract-address post without project discovery
# ══════════════════════════════════════════════════════════════════
def test_scenario_06_random_ca_without_project():
    op_filter = OfficialPageFilter()
    # Random shill posting a contract address with bystander/shill bio
    is_official, reason = op_filter.evaluate(
        text="Ape this gem 0x1234567890123456789012345678901234567890 right now!",
        author_handle="crypto_calls_100x",
        author_bio="Crypto caller and alpha gems daily",
    )
    assert is_official is False
    assert reason is not None
    r_lower = reason.lower()
    assert "influencer" in r_lower or "shill" in r_lower


# ══════════════════════════════════════════════════════════════════
# Scenario 7: Unofficial influencer announcing a token launch
# ══════════════════════════════════════════════════════════════════
def test_scenario_07_unofficial_influencer_announcing():
    op_filter = OfficialPageFilter()
    is_official, reason = op_filter.evaluate(
        text="Turn on notifications guys! Contract dropping soon for the next 100x moonshot! Watching this closely.",
        author_handle="gemhunter99",
        author_name="Alpha Caller",
    )
    assert is_official is False
    assert reason is not None
    r_lower = reason.lower()
    assert "bystander" in r_lower or "shill" in r_lower


# ══════════════════════════════════════════════════════════════════
# Scenario 8: Official project announcing CA soon
# ══════════════════════════════════════════════════════════════════
def test_scenario_08_official_project_announcing_ca_soon():
    detector = EventDetector()
    parser = AnnouncementParser()
    msg = {
        "text": "Official announcement: $NEXUS token launch scheduled for tomorrow at 4 PM UTC. CA soon on Robinhood Chain!",
        "platform": "x",
        "author_id": "NexusProtocol",
    }
    event_type = detector.detect(msg)
    assert event_type == EventType.LAUNCH_INTENT
    parsed = parser.parse(msg, event_type.value)
    assert parsed.ticker == "NEXUS"
    assert parsed.chain_id == "robinhood"


# ══════════════════════════════════════════════════════════════════
# Scenario 9: Official project publishing its contract address
# ══════════════════════════════════════════════════════════════════
def test_scenario_09_official_project_publishes_ca():
    async def _test():
        engine, session = await _setup_session()
        try:
            resolver = CAResolver(session)
            project = Project(
                id=str(uuid.uuid4()),
                name="Nexus Protocol",
                ticker="NEXUS",
                chain_id="robinhood",
                status=ProjectStatus.APPROVED,
                official_x_handle="nexusprotocol",
            )
            session.add(project)
            await session.flush()

            ca_text = "Here is the official contract address for $NEXUS: 0x1111111111111111111111111111111111111111"
            cand, msg = await resolver.process_ca_announcement(
                project=project,
                text=ca_text,
                platform="x",
                sender_id="nexusprotocol",
                sender_username="nexusprotocol",
            )
            assert cand is not None
            assert cand.address.lower() == "0x1111111111111111111111111111111111111111"
            assert cand.is_verified is True
            assert cand.has_conflict is False
        finally:
            await session.close()
            await engine.dispose()

    asyncio.run(_test())


# ══════════════════════════════════════════════════════════════════
# Scenario 10: Duplicate announcement handling
# ══════════════════════════════════════════════════════════════════
def test_scenario_10_duplicate_announcement_deduplication():
    async def _test():
        engine, session = await _setup_session()
        try:
            registry = ProjectRegistry(session)
            parsed = ParsedAnnouncement(
                platform="x",
                message_id="tweet-100",
                post_url="https://x.com/StarProtocol/status/100",
                text="CA soon for $STAR!",
                author_id="StarProtocol",
                event_type="launch_intent",
                published_at=datetime.now(timezone.utc),
                received_at=datetime.now(timezone.utc),
                is_forwarded=False,
                ticker="STAR",
                chain_id="solana",
            )
            proj1, created1 = await registry.get_or_create_project(parsed)
            assert created1 is True

            # Same ticker + chain arriving again
            proj2, created2 = await registry.get_or_create_project(parsed)
            assert created2 is False
            assert proj1.id == proj2.id
        finally:
            await session.close()
            await engine.dispose()

    asyncio.run(_test())


# ══════════════════════════════════════════════════════════════════
# Scenario 11: Gemini API unavailable — safe fallback, no silent approval
# ══════════════════════════════════════════════════════════════════
def test_scenario_11_gemini_api_unavailable():
    async def _test():
        mock_gemini = MagicMock(spec=GeminiClient)
        mock_gemini.is_configured = True
        mock_gemini.generate_structured_json = AsyncMock(side_effect=GeminiAPIError("Connection timed out to Gemini"))

        evaluator = ProjectEvaluator(gemini_client=mock_gemini)
        result = await evaluator.evaluate_project(
            post_text="New trading terminal launch tomorrow. CA soon!",
            author_handle="TerminalTrade",
            session=None,
        )
        # Must NOT be approved; marked for REVIEW with missing info note
        assert result.quality_assessment.recommendation == RecommendationLevel.REVIEW
        assert any("unavailable" in m.lower() for m in result.quality_assessment.missing_information)

    asyncio.run(_test())


# ══════════════════════════════════════════════════════════════════
# Scenario 12: Gemini returns malformed JSON
# ══════════════════════════════════════════════════════════════════
def test_scenario_12_gemini_malformed_json():
    async def _test():
        mock_gemini = MagicMock(spec=GeminiClient)
        mock_gemini.is_configured = True
        mock_gemini.generate_structured_json = AsyncMock(side_effect=GeminiAPIError("Gemini output was not valid JSON"))

        evaluator = ProjectEvaluator(gemini_client=mock_gemini)
        result = await evaluator.evaluate_project(
            post_text="CA dropping soon for $GLITCH",
            author_handle="GlitchProject",
            session=None,
        )
        assert result.quality_assessment.recommendation == RecommendationLevel.REVIEW
        assert any("unavailable" in m.lower() for m in result.quality_assessment.missing_information)

    asyncio.run(_test())


# ══════════════════════════════════════════════════════════════════
# Scenario 13: Telegram approval is clicked twice (Idempotency)
# ══════════════════════════════════════════════════════════════════
def test_scenario_13_telegram_approval_double_click():
    async def _test():
        engine, session = await _setup_session()
        try:
            event_queue = asyncio.Queue()
            bot = TelegramBot(event_queue=event_queue, AsyncSessionLocal=lambda: session)

            project = Project(
                id=str(uuid.uuid4()),
                name="DoubleTapProject",
                ticker="TAP",
                status=ProjectStatus.APPROVED,  # Already approved on first click
            )
            session.add(project)
            await session.flush()

            query = MagicMock()
            query.answer = AsyncMock()

            # Second click
            await bot._handle_approve(
                project=project,
                operator_id=123456789,
                session=session,
                query=query,
            )
            query.answer.assert_called_with("✓ Project is already approved and in CA monitoring.", show_alert=False)
            assert event_queue.empty()  # Did not push duplicate event to queue
        finally:
            await session.close()
            await engine.dispose()

    asyncio.run(_test())


# ══════════════════════════════════════════════════════════════════
# Scenario 14: Rejected project publishes another announcement
# ══════════════════════════════════════════════════════════════════
def test_scenario_14_rejected_project_blocked():
    async def _test():
        engine, session = await _setup_session()
        try:
            matcher = IdentityMatcher(session)
            project = Project(
                id=str(uuid.uuid4()),
                name="ScamToken",
                ticker="SCAM",
                chain_id="solana",
                status=ProjectStatus.REJECTED,
                is_blacklisted=True,
            )
            bl = BlacklistEntry(project_id=project.id, reason="Blacklisted rug")
            session.add_all([project, bl])
            await session.flush()

            is_bl = await matcher.is_blacklisted(ticker="SCAM", chain_id="solana")
            assert is_bl is True
        finally:
            await session.close()
            await engine.dispose()

    asyncio.run(_test())


# ══════════════════════════════════════════════════════════════════
# Scenario 15: Approved project publishes conflicting/unrelated CA
# ══════════════════════════════════════════════════════════════════
def test_scenario_15_conflicting_ca_detection():
    async def _test():
        engine, session = await _setup_session()
        try:
            resolver = CAResolver(session)
            project = Project(
                id=str(uuid.uuid4()),
                name="DualDrop",
                ticker="DUAL",
                chain_id="robinhood",
                status=ProjectStatus.APPROVED,
                official_x_handle="dualdrop",
            )
            session.add(project)
            await session.flush()

            # First valid address
            await resolver.process_ca_announcement(
                project=project,
                text="Official CA: 0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
                platform="x",
                sender_id="dualdrop",
            )

            # Second conflicting address from same account
            cand2, msg = await resolver.process_ca_announcement(
                project=project,
                text="Wait use this CA instead: 0xBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB",
                platform="x",
                sender_id="dualdrop",
            )
            assert cand2 is not None
            assert cand2.has_conflict is True
            assert cand2.conflict_note is not None
            assert "conflict" in cand2.conflict_note.lower()
        finally:
            await session.close()
            await engine.dispose()

    asyncio.run(_test())


# ══════════════════════════════════════════════════════════════════
# Scenario 16: Solana contract fails validation
# ══════════════════════════════════════════════════════════════════
def test_scenario_16_solana_contract_invalid():
    invalid_sol_ca = "InvalidSolanaAddress000000000000"
    assert is_valid_solana_address(invalid_sol_ca) is False

    evm_ca = "0x58A6ff1234567890123456789012345678901234"
    assert is_valid_solana_address(evm_ca) is False


# ══════════════════════════════════════════════════════════════════
# Scenario 17: Robinhood Chain contract fails validation
# ══════════════════════════════════════════════════════════════════
def test_scenario_17_evm_contract_invalid():
    invalid_evm = "0x12345"
    assert is_valid_evm_address(invalid_evm) is False

    sol_ca = "So11111111111111111111111111111111111111112"
    assert is_valid_evm_address(sol_ca) is False


# ══════════════════════════════════════════════════════════════════
# Scenario 18: Trading conditions not satisfied (Tax > 2% or Vol < $100)
# ══════════════════════════════════════════════════════════════════
def test_scenario_18_tax_and_volume_conditions_not_satisfied():
    tax_checker = TaxChecker()
    # Buy tax 5% exceeds max 2%
    tax_res = tax_checker.evaluate_taxes(sim_buy_tax=5.0, sim_sell_tax=1.0)
    assert tax_res.status == CheckResult.FAIL
    assert "exceeds" in tax_res.message.lower()

    vol_tracker = PoolVolumeTracker()
    # Genuine volume only $40 (fails $100 minimum)
    trade = TradeEvent(
        tx_hash="0xTx1",
        wallet_address="0xUser1",
        is_buy=True,
        amount_usd=40.0,
        timestamp=datetime.now(timezone.utc),
    )
    vol_res = vol_tracker.evaluate_volume_snapshot(
        pool_address="0xTestPool",
        recent_trades=[trade],
    )
    assert vol_res.status == CheckResult.FAIL


# ══════════════════════════════════════════════════════════════════
# Scenario 19: RPC Timeout / Executor Failure handling
# ══════════════════════════════════════════════════════════════════
def test_scenario_19_executor_failure_handling():
    async def _test():
        engine, session = await _setup_session()
        try:
            project = Project(
                id=str(uuid.uuid4()),
                name="TimeoutProject",
                ticker="TIME",
                chain_id="robinhood",
                ca_address="0x1111111111111111111111111111111111111111",
                status=ProjectStatus.CHECKS_PASSED,
            )
            session.add(project)
            await session.flush()

            order_mgr = OrderManager(session=session)
            # Mock executor simulating RPC timeout
            mock_evm = MagicMock()
            exec_mock = MagicMock()
            exec_mock.success = False
            exec_mock.error_message = "RPC connection timed out after 60s"
            mock_evm.execute_buy = AsyncMock(return_value=exec_mock)
            order_mgr.evm_exec = mock_evm

            order, pos, msg = await order_mgr.execute_approved_buy(project)
            assert order is not None
            assert order.status == OrderStatus.FAILED
            assert pos is None
            assert project.status == ProjectStatus.SAFETY_BLOCKED
        finally:
            await session.close()
            await engine.dispose()

    asyncio.run(_test())


# ══════════════════════════════════════════════════════════════════
# Scenario 20: Successful dry-run transaction
# ══════════════════════════════════════════════════════════════════
def test_scenario_20_successful_dry_run_transaction():
    async def _test():
        engine, session = await _setup_session()
        try:
            project = Project(
                id=str(uuid.uuid4()),
                name="DryRunToken",
                ticker="DRY",
                chain_id="robinhood",
                ca_address="0x2222222222222222222222222222222222222222",
                status=ProjectStatus.CHECKS_PASSED,
            )
            session.add(project)
            await session.flush()

            order_mgr = OrderManager(session=session)
            order, pos, msg = await order_mgr.execute_approved_buy(project, amount_usd=50.0)

            assert order is not None
            assert order.status == OrderStatus.FILLED
            assert order.is_paper is True  # Paper/dry-run verified
            assert pos is not None
            assert pos.status == PositionStatus.OPEN
            assert project.status == ProjectStatus.PURCHASED
        finally:
            await session.close()
            await engine.dispose()

    asyncio.run(_test())
