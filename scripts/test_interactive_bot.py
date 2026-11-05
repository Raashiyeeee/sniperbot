"""
scripts/test_interactive_bot.py
───────────────────────────────
Live interactive test runner for Telegram operator buttons.
Sends an alert with a real project to your configured Telegram chat
and listens live for button taps (ACCEPT, REJECT, DETAILS).

Run in terminal:
    python scripts/test_interactive_bot.py
"""
import asyncio
import sys
import uuid
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import get_settings
from database.connection import AsyncSessionLocal, create_tables
from database.models import Announcement, EventType, Platform, Project, ProjectStatus
from telegram_bot.alerts import AlertService
from telegram_bot.bot import TelegramBot


async def main():
    settings = get_settings()
    token = settings.telegram_bot_token
    chat_id = settings.telegram_alert_chat_id

    if not token or token == "your_bot_token_here":
        print("❌ Error: TELEGRAM_BOT_TOKEN is not set in .env")
        return

    print("🔧 Initializing database schema...")
    await create_tables()

    # 1. Register a real test project in the database
    test_id = str(uuid.uuid4())
    async with AsyncSessionLocal() as session:
        project = Project(
            id=test_id,
            name="Nova Protocol",
            ticker="NOVA",
            chain_id="robinhood",
            status=ProjectStatus.PENDING_APPROVAL,
            description="Next-generation automated liquidity pool on Robinhood Chain",
            official_x_handle="NovaProtocol",
        )
        session.add(project)

        announcement = Announcement(
            id=str(uuid.uuid4()),
            project_id=test_id,
            platform=Platform.X,
            source_handle="NovaProtocol",
            post_url="https://x.com/NovaProtocol/status/1888888888",
            text="Official announcement: Nova Protocol $NOVA fair launch on Robinhood Chain today!",
            event_type=EventType.LAUNCH_INTENT,
        )
        session.add(announcement)
        await session.commit()

    print(f"📦 Registered project 'Nova Protocol ($NOVA)' in database (ID: {test_id[:8]}...)")

    # 2. Start the Telegram Bot polling loop
    event_queue: asyncio.Queue = asyncio.Queue()
    tg_bot = TelegramBot(event_queue, AsyncSessionLocal)

    async with AsyncSessionLocal() as session:
        alert_service = AlertService(tg_bot.bot, session)
        print(f"🚀 Sending live alert to chat ID: {chat_id}...")
        await alert_service.send_launch_detected(project, announcement)

    print("✅ Alert sent to Telegram!")
    print("\n" + "=" * 60)
    print("🎧 Bot listener is now LIVE in polling mode!")
    print("👉 Tap [ACCEPT], [REJECT], or [DETAILS] in Telegram.")
    print("The message in the chat will visually update immediately!")
    print("Press Ctrl+C to stop.")
    print("=" * 60 + "\n")

    await tg_bot.start()

    # Keep running and print events
    try:
        while True:
            event = await event_queue.get()
            print(f"🔥 [PIPELINE EVENT RECEIVED]: {event}")
    except (asyncio.CancelledError, KeyboardInterrupt):
        pass
    finally:
        await tg_bot.stop()
        print("\n👋 Stopped listener.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nDone.")
