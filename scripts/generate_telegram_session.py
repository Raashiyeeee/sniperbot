#!/usr/bin/env python3
"""
scripts/generate_telegram_session.py
───────────────────────────────────
Interactive CLI helper to log in via Telegram's MTProto API and generate
a secure, permanent TELEGRAM_SESSION_STRING for the userbot.

Steps:
1. Visit https://my.telegram.org -> API development tools.
2. Obtain api_id and api_hash.
3. Run: python3 scripts/generate_telegram_session.py
4. Copy the output TELEGRAM_SESSION_STRING into Render environment variables.
"""

import asyncio
import sys

try:
    from telethon import TelegramClient
    from telethon.sessions import StringSession
except ImportError:
    print("\n❌ Telethon is not installed!")
    print("Run: pip install telethon\n")
    sys.exit(1)


async def main() -> None:
    print("=" * 65)
    print("  🚀 Telegram Userbot Session Generator (Telethon)  ")
    print("=" * 65)
    print("Get your API credentials from: https://my.telegram.org\n")

    api_id_str = input("Enter your API ID: ").strip()
    if not api_id_str.isdigit():
        print("❌ Error: API ID must be a numeric integer.")
        return
    api_id = int(api_id_str)

    api_hash = input("Enter your API Hash: ").strip()
    if not api_hash:
        print("❌ Error: API Hash cannot be empty.")
        return

    print("\nConnecting to Telegram data centers...")
    client = TelegramClient(StringSession(), api_id, api_hash)
    await client.start()

    session_string = client.session.save()
    me = await client.get_me()

    username = f"@{me.username}" if me.username else "No Username"
    print("\n" + "=" * 65)
    print("🎉 SUCCESS! You are authorized as:")
    print(f"   Name:     {me.first_name} {me.last_name or ''}".strip())
    print(f"   Username: {username}")
    print(f"   User ID:  {me.id}")
    print("=" * 65)
    print("\n📋 Your TELEGRAM_SESSION_STRING (keep this SECRET):\n")
    print("-" * 65)
    print(session_string)
    print("-" * 65)
    print("\n✅ What to do next:")
    print("1. Go to your Render Dashboard -> Environment.")
    print("2. Add these variables:")
    print(f"   TELEGRAM_API_ID = {api_id}")
    print(f"   TELEGRAM_API_HASH = {api_hash}")
    print(f"   TELEGRAM_SESSION_STRING = {session_string}")
    print("3. (Optional) Set TELEGRAM_USERBOT_CHATS with the group name or ID.")
    print("4. Save & Deploy. Your bot will now snipe from that private group 24/7!\n")

    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())

