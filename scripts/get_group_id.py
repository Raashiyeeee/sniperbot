"""
scripts/get_group_id.py
───────────────────────
Helper script to detect your Telegram group ID.
Make sure you have added your bot to the group, then send any message
(like "hello") in the group, and run:
    python scripts/get_group_id.py
"""
import asyncio
import sys
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import httpx
from config.settings import get_settings


async def main():
    settings = get_settings()
    token = settings.telegram_bot_token

    if not token or token == "your_bot_token_here":
        print("❌ Error: TELEGRAM_BOT_TOKEN is not set in .env")
        return

    print("🔍 Fetching recent updates from Telegram...")
    url = f"https://api.telegram.org/bot{token}/getUpdates"

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.get(url)
            data = resp.json()
        except Exception as exc:
            print(f"❌ Network error: {exc}")
            return

    if not data.get("ok"):
        print(f"❌ Telegram API error: {data}")
        return

    results = data.get("result", [])
    found_groups = {}

    for update in results:
        msg = update.get("message") or update.get("channel_post") or update.get("my_chat_member")
        if not msg:
            continue
        chat = msg.get("chat", {})
        chat_type = chat.get("type")
        chat_id = chat.get("id")
        title = chat.get("title") or chat.get("first_name") or "Unknown"

        if chat_type in ("group", "supergroup", "channel") and chat_id:
            found_groups[chat_id] = (title, chat_type)

    if found_groups:
        print("\n🎉 Found your group(s):")
        for gid, (gtitle, gtype) in found_groups.items():
            print(f"  • Name: '{gtitle}' | Type: {gtype} | Chat ID: {gid}")

        # Pick the most recent one
        selected_gid = list(found_groups.keys())[-1]
        print(f"\n👉 Set this in your .env file:")
        print(f"TELEGRAM_ALERT_CHAT_ID={selected_gid}\n")
    else:
        print("\n⚠️ No group messages found yet!")
        print("Please do this:")
        print("1. Open your Telegram group.")
        print("2. Make sure your bot is in the group as an admin.")
        print("3. Type any message (e.g. 'hello') into the group.")
        print("4. Run 'python scripts/get_group_id.py' again!\n")


if __name__ == "__main__":
    asyncio.run(main())

