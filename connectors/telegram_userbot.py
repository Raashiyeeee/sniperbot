"""
connectors/telegram_userbot.py
──────────────────────────────
Telegram MTProto client (Userbot) listener using Telethon.

Allows monitoring private groups, invite-only groups, and groups with anti-bot
CAPTCHAs by connecting through an authorized human Telegram account session.
Listens to real-time NewMessage and MessageEdited events, normalizes payloads,
and feeds them directly into the detection and execution queue.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Optional

import structlog

from config.settings import get_settings

logger = structlog.get_logger(__name__)


class TelegramUserbotConnector:
    """Listens for Telegram messages using a real user session (Telethon)."""

    def __init__(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self.queue = queue
        self.settings = get_settings()
        self._client: Any = None
        self._running = False
        self._stop_event = asyncio.Event()

    @property
    def is_configured(self) -> bool:
        """Check if Telethon credentials and session string are provided."""
        return bool(
            self.settings.telegram_api_id
            and self.settings.telegram_api_hash
            and (self.settings.telegram_session_string or "").strip()
        )

    async def start(self) -> None:
        """Connect to Telegram MTProto and start event dispatch loop."""
        if not self.is_configured:
            logger.info("telegram_userbot_disabled", reason="Credentials or session string not set in .env")
            return

        try:
            from telethon import TelegramClient, events
            from telethon.sessions import StringSession
        except ImportError:
            logger.warning(
                "telegram_userbot_telethon_not_installed",
                msg="Install telethon (pip install telethon) to enable userbot private group sniping",
            )
            return

        api_id = int(self.settings.telegram_api_id)
        api_hash = self.settings.telegram_api_hash.strip()
        session_str = self.settings.telegram_session_string.strip()

        logger.info("telegram_userbot_initializing", api_id=api_id)

        try:
            self._client = TelegramClient(StringSession(session_str), api_id, api_hash)
            await self._client.connect()

            if not await self._client.is_user_authorized():
                logger.error(
                    "telegram_userbot_unauthorized",
                    msg="Session string is invalid or expired. Re-generate session with scripts/generate_telegram_session.py",
                )
                return

            me = await self._client.get_me()
            username = getattr(me, "username", None) or "Anonymous"
            logger.info("telegram_userbot_authorized", account_id=me.id, username=f"@{username}")

            # Parse chat filter if configured
            configured_chats = [
                c.strip().lower()
                for c in (self.settings.telegram_userbot_chats or "").split(",")
                if c.strip()
            ]

            @self._client.on(events.NewMessage)
            @self._client.on(events.MessageEdited)
            async def _handle_message_event(event: Any) -> None:
                try:
                    text = event.raw_text or ""
                    if not text:
                        return

                    chat = await event.get_chat()
                    chat_id_str = str(event.chat_id)
                    chat_title = getattr(chat, "title", None) or chat_id_str
                    chat_username = getattr(chat, "username", None) or ""

                    # Filter by chat if targets are explicitly listed
                    if configured_chats:
                        matched = False
                        for target in configured_chats:
                            if (
                                target == chat_id_str
                                or (chat_username and target == f"@{chat_username.lower()}")
                                or (chat_username and target == chat_username.lower())
                                or (target in chat_title.lower())
                            ):
                                matched = True
                                break
                        if not matched:
                            return

                    sender = await event.get_sender()
                    sender_id = str(event.sender_id or "")
                    sender_username = getattr(sender, "username", None)
                    sender_name = (
                        f"{getattr(sender, 'first_name', '')} {getattr(sender, 'last_name', '')}".strip()
                        if sender
                        else "Unknown"
                    )

                    payload = {
                        "id": f"tg_ub_{event.chat_id}_{event.id}",
                        "source": "telegram",
                        "platform": "telegram",
                        "text": text,
                        "chat_id": chat_id_str,
                        "chat_title": chat_title,
                        "chat_username": chat_username,
                        "author_id": sender_id,
                        "author_username": sender_username,
                        "author_name": sender_name,
                        "is_forwarded": bool(getattr(event.message, "fwd_from", None)),
                        "message_id": event.id,
                        "created_at": (
                            event.date.isoformat()
                            if getattr(event, "date", None)
                            else datetime.now(timezone.utc).isoformat()
                        ),
                    }

                    try:
                        self.queue.put_nowait(payload)
                        logger.info(
                            "telegram_userbot_message_enqueued",
                            chat=chat_title,
                            chat_id=chat_id_str,
                            length=len(text),
                            has_0x="0x" in text,
                        )
                    except asyncio.QueueFull:
                        logger.warning("telegram_userbot_queue_full", chat_id=chat_id_str)

                except Exception as exc:
                    logger.error("telegram_userbot_handler_error", error=str(exc))

            self._running = True
            logger.info("telegram_userbot_listening_started", target_chats=configured_chats or "ALL")
            await self._client.run_until_disconnected()

        except Exception as exc:
            logger.error("telegram_userbot_fatal_error", error=str(exc))
        finally:
            self._running = False
            logger.info("telegram_userbot_stopped")

    async def stop(self) -> None:
        """Disconnect client cleanly."""
        if self._client and self._client.is_connected():
            await self._client.disconnect()
        self._running = False
