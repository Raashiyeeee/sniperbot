"""
scripts/test_telegram.py
────────────────────────
Sends a test Launch Radar alert with inline buttons to your Telegram chat.
Run this from your terminal outside the sandbox to verify your bot credentials:
    python scripts/test_telegram.py
"""
import asyncio
import sys
from pathlib import Path

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import httpx

from config.settings import get_settings

async def main():
    settings = get_settings()
    token = settings.telegram_bot_token
    chat_id = settings.telegram_alert_chat_id

    if not token or token == "your_bot_token_here":
        print("❌ Error: TELEGRAM_BOT_TOKEN is not set in .env")
        return

    print(f"🚀 Sending test alert to Telegram chat ID: {chat_id}...")

    url = f"https://api.telegram.org/bot{token}/sendMessage"

    text = (
        "🚨 *LAUNCH RADAR TEST — PENDING APPROVAL*\n\n"
        "*Project:* StarToken ($STAR)\n"
        "*Chain:* Robinhood Chain (EVM)\n"
        "*Source:* X — https://x.com/StarTokenDev\n"
        "*Status:* PENDING APPROVAL\n\n"
        "Tap a button below to test operator response:"
    )

    inline_keyboard = {
        "inline_keyboard": [
            [
                {"text": "✅ ACCEPT", "callback_data": "approve:test-uuid"},
                {"text": "❌ REJECT", "callback_data": "reject:test-uuid"},
                {"text": "📋 DETAILS", "callback_data": "details:test-uuid"},
            ]
        ]
    }

    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
        "reply_markup": inline_keyboard,
    }

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.post(url, json=payload)
            if resp.status_code == 200:
                print("✅ Success! Test message sent to your Telegram. Check your chat!")
            else:
                print(f"❌ Telegram API Error ({resp.status_code}): {resp.text}")
        except Exception as exc:
            print(f"❌ Network Error: {exc}")

if __name__ == "__main__":
    asyncio.run(main())

