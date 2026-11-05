"""
tests/test_telegram_bot.py
──────────────────────────
Unit & integration tests for AlertService and TelegramBot callback workflows.
"""
from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from config.settings import settings
from database.models import (
    Announcement,
    Base,
    EventType,
    Platform,
    Project,
    ProjectStatus,
)
from telegram_bot.alerts import AlertService
from telegram_bot.bot import TelegramBot


async def _setup_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    session = session_maker()
    return engine, session, session_maker


def test_alert_service_send_launch_detected():
    async def _test():
        engine, session, _ = await _setup_session()
        try:
            mock_bot = AsyncMock()
            mock_sent_msg = MagicMock()
            mock_sent_msg.message_id = 999
            mock_sent_msg.chat_id = -100123456
            mock_bot.send_message.return_value = mock_sent_msg

            alert_service = AlertService(mock_bot, session)

            project = Project(
                id=str(uuid.uuid4()),
                name="MoonMission",
                ticker="MOON",
                chain_id="robinhood",
                status=ProjectStatus.PENDING_APPROVAL,
            )
            session.add(project)
            await session.commit()

            announcement = Announcement(
                id=str(uuid.uuid4()),
                project_id=project.id,
                platform=Platform.X,
                source_handle="@MoonDev",
                post_url="https://x.com/MoonDev/status/123",
                event_type=EventType.LAUNCH_INTENT,
            )
            session.add(announcement)
            await session.commit()

            notification = await alert_service.send_launch_detected(project, announcement)

            assert notification.id is not None
            assert notification.message_id == 999
            assert notification.notification_type == "launch_detected"
            assert mock_bot.send_message.called
            call_kwargs = mock_bot.send_message.call_args.kwargs
            assert "New launch announcement detected" in call_kwargs["text"]
            assert "MoonMission" in call_kwargs["text"]
            assert call_kwargs["reply_markup"] is not None

        finally:
            await session.close()
            await engine.dispose()

    asyncio.run(_test())


def test_alert_service_send_ca_detected_and_expired():
    async def _test():
        engine, session, _ = await _setup_session()
        try:
            mock_bot = AsyncMock()
            mock_bot.send_message.return_value = MagicMock()

            alert_service = AlertService(mock_bot, session)

            project = Project(
                id=str(uuid.uuid4()),
                name="StarDust",
                ticker="DUST",
                chain_id="solana",
                status=ProjectStatus.APPROVED,
            )
            session.add(project)
            await session.commit()

            await alert_service.send_ca_detected(
                project=project,
                ca_address="4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R",
                chain_id="solana",
            )
            assert mock_bot.send_message.called
            assert "CA DETECTED" in mock_bot.send_message.call_args.kwargs["text"]

            # Test reminder
            await alert_service.send_reminder(project, hours_elapsed=1.0, hours_until_expiry=3.0)
            assert "REMINDER" in mock_bot.send_message.call_args.kwargs["text"]

            # Test expired
            await alert_service.send_expired(project)
            assert "EXPIRED" in mock_bot.send_message.call_args.kwargs["text"]

        finally:
            await session.close()
            await engine.dispose()

    asyncio.run(_test())


def test_telegram_bot_handle_reject_and_approve():
    async def _test():
        engine, session, session_maker = await _setup_session()
        try:
            event_queue = asyncio.Queue()
            tg_bot = TelegramBot(event_queue, session_maker)

            # Ensure operator is authorised
            op_id = settings.operator_ids[0] if settings.operator_ids else 1386285936
            if op_id not in settings.operator_ids:
                settings.telegram_operator_ids = str(op_id)

            # 1. Test Reject
            proj_reject = Project(
                id=str(uuid.uuid4()),
                name="ScamToken",
                ticker="SCAM",
                chain_id="robinhood",
                status=ProjectStatus.PENDING_APPROVAL,
            )
            session.add(proj_reject)
            await session.commit()

            mock_query = AsyncMock()
            mock_query.from_user.id = op_id
            mock_query.data = f"reject:{proj_reject.id}"
            mock_query.edit_message_text = AsyncMock()
            mock_query.answer = AsyncMock()

            mock_update = MagicMock()
            mock_update.callback_query = mock_query

            await tg_bot._dispatch_callback(mock_update, MagicMock())

            async with session_maker() as verify_session:
                refreshed = await verify_session.get(Project, proj_reject.id)
                assert refreshed.status == ProjectStatus.REJECTED
                assert refreshed.is_blacklisted is True

            assert mock_query.edit_message_text.called
            edit_text = mock_query.edit_message_text.call_args.kwargs["text"]
            assert "REJECTED & BLACKLISTED" in edit_text
            assert str(op_id) in edit_text
            assert mock_query.answer.called

            # 2. Test Approve
            proj_approve = Project(
                id=str(uuid.uuid4()),
                name="GemToken",
                ticker="GEM",
                chain_id="robinhood",
                status=ProjectStatus.PENDING_APPROVAL,
            )
            session.add(proj_approve)
            await session.commit()

            mock_query_app = AsyncMock()
            mock_query_app.from_user.id = op_id
            mock_query_app.data = f"approve:{proj_approve.id}"
            mock_query_app.edit_message_text = AsyncMock()
            mock_query_app.answer = AsyncMock()

            mock_update_app = MagicMock()
            mock_update_app.callback_query = mock_query_app

            await tg_bot._dispatch_callback(mock_update_app, MagicMock())

            async with session_maker() as verify_session:
                refreshed_app = await verify_session.get(Project, proj_approve.id)
                assert refreshed_app.status == ProjectStatus.APPROVED

            assert mock_query_app.edit_message_text.called
            edit_text_app = mock_query_app.edit_message_text.call_args.kwargs["text"]
            assert "APPROVED" in edit_text_app
            assert str(op_id) in edit_text_app
            assert mock_query_app.answer.called

            # Check queue event
            queued = await event_queue.get()
            assert queued["event"] == "approved"
            assert queued["project_id"] == proj_approve.id

            # 3. Test Test-UUID callback
            mock_query_test = AsyncMock()
            mock_query_test.from_user.id = op_id
            mock_query_test.data = "reject:test-uuid"
            mock_query_test.edit_message_text = AsyncMock()
            mock_query_test.answer = AsyncMock()

            mock_update_test = MagicMock()
            mock_update_test.callback_query = mock_query_test

            await tg_bot._dispatch_callback(mock_update_test, MagicMock())
            assert mock_query_test.edit_message_text.called
            assert "LAUNCH TEST — REJECTED" in mock_query_test.edit_message_text.call_args.kwargs["text"]

        finally:
            await session.close()
            await engine.dispose()

    asyncio.run(_test())
