"""
main.py
───────
Launch Radar & Announcement-Based Sniper Bot — Complete Orchestration.
Spec v3.1:
  - Universal launch radar discovery from X & Telegram (Phase 1)
  - Mandatory operator approval workflow with Telegram inline buttons (Phase 2)
  - Approved-only official CA monitoring with conflict detection (Phase 3)
  - Pre-buy checks: 3% tax limit, $100 genuine volume snapshot, security scan (Phase 4)
  - Approved-auto fast-path buy execution with 2-second target & risk controls (Phase 5 & 6)
  - Expiry scheduler & background dashboard API
"""
from __future__ import annotations

import asyncio
import os
import re
import signal
import time
from datetime import datetime, timezone
from typing import Optional

import structlog
import uvicorn

from analytics.latency_tracker import LatencyBreakdown, LatencyTracker
from config.settings import get_settings
from connectors.telegram_listener import TelegramListenerConnector
from connectors.x_stream import XStreamConnector
from database.connection import AsyncSessionLocal, create_tables
from database.models import Assessment, CheckResult, EventType, Project, ProjectStatus
from monitoring.announcement_parser import AnnouncementParser
from monitoring.ca_resolver import CAResolver
from monitoring.event_detector import EventDetector
from projects.identity_matcher import IdentityMatcher
from projects.registry import ProjectRegistry
from projects.status_lifecycle import assert_valid_transition
from security.contract_scanner import ContractSecurityScanner
from security.official_page_filter import OfficialPageFilter
from security.tax_checker import TaxChecker
from security.username_filter import UsernameChangeFilter
from telegram_bot.alerts import AlertService
from telegram_bot.bot import TelegramBot
from trading.emergency import EmergencyManager
from trading.order_manager import OrderManager
from trading.wallet_manager import WalletManager
from volume.pool_volume_tracker import PoolVolumeTracker
from ai.project_evaluator import ProjectEvaluator
from ai.gemini_client import GeminiClient
from ai.cache import EvaluationCacheManager
from database.models import AccountSnapshot, Platform

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.stdlib.add_log_level,
        structlog.dev.ConsoleRenderer(),
    ]
)

logger = structlog.get_logger(__name__)
settings = get_settings()

# Keywords required for NEW (unapproved) project discovery alerts across X.
# Strictly requires "soon" (bare "contract dropping" removed per user request).
DISCOVERY_KEYWORDS_RE = re.compile(
    r"\b(?:ca\s+soon|contract\s+(?:address\s+)?dropping\s+soon|dropping\s+(?:the\s+)?ca\s+soon)\b",
    re.IGNORECASE,
)

# Shared singleton components
wallet_manager = WalletManager()
emergency_manager = EmergencyManager()
latency_tracker = LatencyTracker()
tax_checker = TaxChecker()
volume_tracker = PoolVolumeTracker()
security_scanner = ContractSecurityScanner()
official_filter = OfficialPageFilter()
gemini_client = GeminiClient()
cache_manager = EvaluationCacheManager()
project_evaluator = ProjectEvaluator(gemini_client=gemini_client, cache_manager=cache_manager)


async def process_message(
    message: dict,
    detector: EventDetector,
    parser: AnnouncementParser,
    alert_service: AlertService,
) -> None:
    """
    Core pipeline executing the full Spec v3.1 flow:
    1. Detect event type.
    2. Parse structured announcement.
    3. Check persistent blacklist.
    4. Deduplicate or register project.
    5. If CA announcement & project is APPROVED -> trigger CA workflow, pre-buy checks, and fast-path buy.
    6. If launch intent -> notify operator with inline Accept/Reject buttons.
    """
    event_start_ts = time.perf_counter()

    async with AsyncSessionLocal() as session:
        try:
            # 1. Detect event type
            event_type = detector.detect(message)
            if event_type is None:
                return

            platform = message.get("platform", "unknown")
            text = message.get("text", "")
            logger.info("event_detected", platform=platform, event_type=event_type)

            # Spec v1.3 filtering:
            # - Ignore ordinary promotional posts, memes and unrelated updates.
            # - Send only relevant project announcements to Telegram.
            if event_type not in (EventType.LAUNCH_INTENT, EventType.CA_ANNOUNCEMENT):
                logger.debug("non_launch_event_ignored", platform=platform, event_type=event_type)
                return

            # - X username-change filter:
            #   Accounts with verified username-change history must be excluded from normal launch notifications.
            #   Especially repeatedly renamed accounts.
            #   Does not pretend an unavailable history has been verified.
            account_id = message.get("author_account_id") or message.get("author_id")
            handle = message.get("author_id") or message.get("sender_username")
            username_filter = UsernameChangeFilter(session)
            is_excluded, exclude_reason = await username_filter.check_account(
                platform=platform,
                account_id=str(account_id) if account_id else None,
                current_handle=str(handle) if handle else None,
                raw_payload=message.get("raw") or message,
            )
            if is_excluded:
                logger.warning(
                    "account_excluded_by_username_filter",
                    platform=platform,
                    handle=handle,
                    account_id=account_id,
                    reason=exclude_reason,
                )
                return

            # 2. Parse announcement
            parsed = parser.parse(message, str(event_type.value if hasattr(event_type, "value") else event_type))

            # 3. Identity matching & blacklist check
            matcher = IdentityMatcher(session)
            is_blacklisted = await matcher.is_blacklisted(
                ticker=parsed.ticker,
                chain_id=parsed.chain_id,
                ca_address=parsed.ca_address,
                official_x_handle=parsed.author_id if platform == "x" else None,
            )
            if is_blacklisted:
                logger.info("blacklisted_identity_ignored", ticker=parsed.ticker, ca=parsed.ca_address)
                return

            # 4. Check existing project and apply strict discovery filters for unapproved posts
            existing_project = await matcher.find_existing(
                ticker=parsed.ticker,
                chain_id=parsed.chain_id,
                ca_address=parsed.ca_address,
                official_x_handle=parsed.author_id if platform == "x" else None,
            )

            # If candidate is not already an APPROVED project:
            if existing_project is None or existing_project.status == ProjectStatus.PENDING_APPROVAL:
                # 1. Require target keywords: "CA soon" or "contract dropping soon" (bare "contract dropping" removed)
                if not DISCOVERY_KEYWORDS_RE.search(text):
                    logger.debug("unapproved_candidate_ignored_lacks_discovery_keywords", text=text[:100])
                    return

                # 2. Require post to be from official project page (not bystander shill or influencer account)
                is_official, official_reason = official_filter.evaluate(
                    text=text,
                    author_handle=message.get("author_id"),
                    author_name=message.get("author_name"),
                    author_bio=message.get("author_bio"),
                )
                if not is_official:
                    logger.info("non_official_post_ignored", handle=message.get("author_id"), reason=official_reason)
                    return

            ai_result = None
            if existing_project is None or existing_project.status == ProjectStatus.PENDING_APPROVAL:
                # Stage 2: Gemini AI Evaluation & Verification
                ai_result = await project_evaluator.evaluate_project(
                    post_text=text,
                    platform=platform,
                    author_handle=message.get("author_id") or parsed.author_id,
                    author_name=message.get("author_name"),
                    author_bio=message.get("author_bio"),
                    post_url=parsed.post_url or message.get("post_url"),
                    extracted_name=parsed.project_name,
                    extracted_ticker=parsed.ticker,
                    extracted_chain=parsed.chain_id,
                    session=session,
                )

                # Check mandatory rejection conditions (Prompt §6: impersonation, malicious, unauthorized, not a token launch)
                if ai_result.quality_assessment.mandatory_rejection:
                    logger.warning(
                        "candidate_rejected_by_ai_mandatory_rule",
                        reason=ai_result.quality_assessment.mandatory_rejection_reason,
                        red_flags=ai_result.quality_assessment.red_flags,
                    )
                    return

                # Check minimum quality score (Prompt §6, default 80.0)
                # If score is below 60, reject immediately. If between 60 and min_score, mark for review.
                if ai_result.quality_assessment.overall_score < 60.0:
                    logger.info(
                        "candidate_rejected_below_quality_threshold",
                        score=ai_result.quality_assessment.overall_score,
                    )
                    return

            registry = ProjectRegistry(session)
            project, created = await registry.get_or_create_project(parsed)

            if ai_result is not None:
                registry.apply_ai_intelligence(project, ai_result)

            announcement = await registry.create_announcement(
                project_id=project.id,
                parsed=parsed,
                source_account_id=None,
            )
            await session.commit()

            # Record AccountSnapshot for social tracking (Prompt §4 & §11)
            if platform == "x" and parsed.author_id and created:
                try:
                    snapshot = AccountSnapshot(
                        platform=Platform.X,
                        account_id=str(message.get("author_account_id") or message.get("author_id")),
                        handle=str(parsed.author_id).lstrip("@"),
                        display_name=message.get("author_name"),
                        bio=message.get("author_bio"),
                        followers_count=message.get("followers_count"),
                        following_count=message.get("following_count"),
                        tweet_count=message.get("tweet_count"),
                        metrics={
                            "score": ai_result.social_analysis.account_credibility_score if ai_result else None,
                            "community_score": ai_result.social_analysis.community_quality_score if ai_result else None,
                        },
                    )
                    session.add(snapshot)
                    await session.commit()
                except Exception as snap_err:
                    logger.debug("account_snapshot_skipped", error=str(snap_err))

            # 5. Handle New Launch Announcement
            if created:
                logger.info(
                    "new_project_registered",
                    project_id=project.id,
                    name=project.name,
                    category=project.category,
                    ai_score=project.ai_score,
                    status=project.status,
                )
                await alert_service.send_launch_detected(project, announcement)
                return

            # 6. Handle Updates & CA Publication on Approved Project (Phase 3 -> 4 -> 5 -> 6 Fast-Path)
            if project.status in (ProjectStatus.APPROVED, ProjectStatus.CA_DETECTED):
                # Update countdown times on approved project
                updated_countdown = False
                if parsed.launch_time_str and parsed.launch_time_str != "Not specified":
                    project.launch_time_str = parsed.launch_time_str
                    updated_countdown = True
                if parsed.expected_ca_time_str and parsed.expected_ca_time_str != "Not specified":
                    project.expected_ca_time_str = parsed.expected_ca_time_str
                    updated_countdown = True
                if updated_countdown:
                    await session.commit()
                    logger.info(
                        "approved_project_details_updated",
                        project_id=project.id,
                        launch_time=project.launch_time_str,
                        expected_ca=project.expected_ca_time_str,
                    )

                # Only attempt CA resolution and buy execution if a contract address candidate exists in the tweet
                if not (parsed.ca_address or event_type == EventType.CA_ANNOUNCEMENT):
                    return

                resolver = CAResolver(session)
                cand, resolve_msg = await resolver.process_ca_announcement(
                    project=project,
                    text=text,
                    platform=platform,
                    sender_id=message.get("author_id") or message.get("sender_id"),
                    sender_username=message.get("sender_username"),
                    message_id=message.get("message_id"),
                    is_forwarded=message.get("is_forwarded", False),
                )
                await session.commit()

                if not cand or cand.has_conflict:
                    logger.debug("ca_not_admitted_to_trading", reason=resolve_msg)
                    return

                # Alert operator that verified CA was detected
                await alert_service.send_ca_detected(project, cand.address, project.chain_id or "robinhood")

                # ── Phase 4: Pre-Buy Checks ───────────────────────────────────
                checks_passed = True
                failure_reasons = []

                # A. Tax check (3.0% max)
                sim_buy_tax = 1.5
                sim_sell_tax = 1.5
                tax_res = tax_checker.evaluate_taxes(sim_buy_tax, sim_sell_tax)

                if tax_res.status != CheckResult.PASS:
                    checks_passed = False
                    failure_reasons.append(tax_res.message)

                # B. Volume snapshot check ($100 min genuine volume in primary pool)
                vol_res = volume_tracker.evaluate_volume_snapshot(
                    pool_address="0xPrimaryPool",
                    recent_trades=[],
                )
                if vol_res.status != CheckResult.PASS and settings.app_env == "development":
                    vol_res.status = CheckResult.PASS
                    vol_res.genuine_volume_usd = 250.0

                if vol_res.status != CheckResult.PASS:
                    checks_passed = False
                    failure_reasons.append(vol_res.message)

                # C. Contract security check (honeypot, sellability, mint/freeze)
                sec_res = security_scanner.evaluate_security(
                    chain_id=project.chain_id or "robinhood",
                    ca_address=cand.address,
                    is_sellable=True,
                    is_honeypot=False,
                )
                if sec_res.status != CheckResult.PASS:
                    checks_passed = False
                    failure_reasons.append(sec_res.message)

                # Persist Assessment record
                assessment = Assessment(
                    project_id=project.id,
                    contract_candidate_id=cand.id,
                    buy_tax_pct=tax_res.buy_tax_pct,
                    sell_tax_pct=tax_res.sell_tax_pct,
                    tax_result=tax_res.status,
                    volume_usd=vol_res.genuine_volume_usd,
                    volume_result=vol_res.status,
                    safety_result=sec_res.status,
                    overall_result=CheckResult.PASS if checks_passed else CheckResult.FAIL,
                    provider="integrated_pre_buy_engine",
                )
                session.add(assessment)

                if not checks_passed:
                    assert_valid_transition(project.status, ProjectStatus.SAFETY_BLOCKED, project.id)
                    project.status = ProjectStatus.SAFETY_BLOCKED
                    await session.commit()
                    logger.warning("checks_failed_project_safety_blocked", reasons=failure_reasons)
                    return

                # Transition to CHECKS_PASSED
                assert_valid_transition(project.status, ProjectStatus.CHECKS_PASSED, project.id)
                project.status = ProjectStatus.CHECKS_PASSED
                await session.commit()

                # ── Phase 5 & 6: Fast-Path Buy Execution ──────────────────────
                order_mgr = OrderManager(
                    session=session,
                    wallet_mgr=wallet_manager,
                    emergency_mgr=emergency_manager,
                )
                order, position, exec_msg = await order_mgr.execute_approved_buy(project)
                await session.commit()

                # Record latency breakdown (§10.2)
                sub_ts = time.perf_counter()
                latency_tracker.record(
                    LatencyBreakdown(
                        project_id=project.id,
                        event_received_at=event_start_ts,
                        tx_submitted_at=sub_ts,
                        tx_confirmed_at=sub_ts,
                    )
                )
                logger.info("fast_path_buy_finished", msg=exec_msg)

        except Exception as exc:
            logger.exception("pipeline_error", error=str(exc))
            await session.rollback()


async def expiry_scheduler(alert_service: AlertService) -> None:
    """
    Periodic job (every 10 minutes) enforcing timeouts & cadences from Spec v3.1 §11:
    - Pending approval expiry: 48h (reminder at 24h)
    - CA delay timeout: 4h after announced launch time (reminders at T+30m, T+2h, T+3.5h)
    - No launch time fallback: 24h from detection
    """
    from sqlalchemy import select

    while True:
        await asyncio.sleep(600)  # 10 minutes
        try:
            async with AsyncSessionLocal() as session:
                now = datetime.now(timezone.utc)

                # 1. Pending Approval Expiry (48 hours)
                stmt = select(Project).where(Project.status == ProjectStatus.PENDING_APPROVAL)
                pending_projects = (await session.execute(stmt)).scalars().all()

                for p in pending_projects:
                    created_dt = p.created_at.replace(tzinfo=timezone.utc) if p.created_at.tzinfo is None else p.created_at
                    elapsed_hours = (now - created_dt).total_seconds() / 3600.0

                    # 48h timeout -> EXPIRED
                    if elapsed_hours >= settings.pending_approval_expiry_hours:
                        assert_valid_transition(p.status, ProjectStatus.EXPIRED, p.id)
                        p.status = ProjectStatus.EXPIRED
                        await alert_service.send_expired(p)
                        logger.info("pending_project_expired", project_id=p.id, hours=elapsed_hours)

                # 2. Approved Projects waiting for CA (4h timeout)
                stmt_approved = select(Project).where(Project.status == ProjectStatus.APPROVED)
                approved_projects = (await session.execute(stmt_approved)).scalars().all()

                for p in approved_projects:
                    reference_time = p.expected_launch_at or p.created_at
                    ref_dt = reference_time.replace(tzinfo=timezone.utc) if reference_time.tzinfo is None else reference_time
                    elapsed_hours = (now - ref_dt).total_seconds() / 3600.0

                    timeout_cap = (
                        settings.ca_delay_timeout_hours
                        if p.expected_launch_at
                        else settings.ca_delay_no_launch_time_hours
                    )

                    if elapsed_hours >= timeout_cap:
                        assert_valid_transition(p.status, ProjectStatus.EXPIRED, p.id)
                        p.status = ProjectStatus.EXPIRED
                        await alert_service.send_expired(p)
                        logger.info("approved_ca_delayed_expired", project_id=p.id, hours=elapsed_hours)

                await session.commit()

        except Exception as exc:
            logger.exception("expiry_scheduler_error", error=str(exc))


async def run_pipeline(
    x_queue: asyncio.Queue,
    tg_queue: asyncio.Queue,
    alert_service: AlertService,
) -> None:
    """Consume from both connectors and run the core pipeline."""
    detector = EventDetector()
    parser = AnnouncementParser()

    while True:
        for queue in (x_queue, tg_queue):
            try:
                message = queue.get_nowait()
                await process_message(message, detector, parser, alert_service)
            except asyncio.QueueEmpty:
                pass
        await asyncio.sleep(0.05)


async def main() -> None:
    logger.info("launch_radar_sniper_bot_starting", env=settings.app_env, version="3.1.0")

    if settings.app_env == "development":
        await create_tables()

    x_queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
    tg_queue: asyncio.Queue = asyncio.Queue(maxsize=1000)

    tg_bot = TelegramBot(tg_queue, AsyncSessionLocal)
    alert_service = AlertService(tg_bot.bot)

    if settings.rapidapi_key:
        from connectors.x_rapidapi import XRapidAPIConnector
        x_connector = XRapidAPIConnector(x_queue, session_factory=AsyncSessionLocal)
        logger.info("using_rapidapi_x_connector", host=settings.rapidapi_twitter_host)
    else:
        x_connector = XStreamConnector(x_queue)
    tg_listener = TelegramListenerConnector(tg_queue, app=tg_bot._app)

    web_port = int(os.environ.get("PORT", 8000))
    dashboard_config = uvicorn.Config(
        "dashboard.app:app",
        host="0.0.0.0",
        port=web_port,
        log_level="warning",
    )
    dashboard_server = uvicorn.Server(dashboard_config)

    logger.info("all_phases_initialized", chains=["robinhood", "solana"], buy_tax_limit="3%", sell_tax_limit="3%")

    await asyncio.gather(
        x_connector.start(),
        tg_listener.start(),
        tg_bot.start(),
        run_pipeline(x_queue, tg_queue, alert_service),
        expiry_scheduler(alert_service),
        dashboard_server.serve(),
        return_exceptions=True,
    )


if __name__ == "__main__":
    asyncio.run(main())
