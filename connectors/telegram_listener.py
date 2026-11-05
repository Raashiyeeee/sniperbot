"""
connectors/telegram_listener.py
--------------------------------
Telegram channel/group listener connector for the token sniper bot.

Uses python-telegram-bot v21 (PTB) Application in polling mode to receive
messages from configured chats and enqueues normalised dicts onto a shared
asyncio.Queue.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Optional

import structlog
from telegram import Message, Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters

from config.settings import get_settings

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _iso_now() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _message_to_dict(message: Message) -> dict[str, Any]:
    """
    Convert a PTB Message object into the canonical normalised dict format.

    Parameters
    ----------
    message:
        The incoming Telegram message.

    Returns
    -------
    dict
        Normalised payload ready to be placed onto the queue.
    """
    # Resolve sender details (None for anonymous channel posts).
    sender_id: Optional[str] = None
    sender_username: Optional[str] = None
    if message.from_user is not None:
        sender_id = str(message.from_user.id)
        sender_username = message.from_user.username  # may still be None

    # Published timestamp.
    published_at = ""
    if message.date is not None:
        published_at = message.date.isoformat()

    # Forwarded-message metadata.
    is_forwarded = message.forward_origin is not None
    forward_origin: Optional[dict[str, Any]] = None
    if message.forward_origin is not None:
        # PTB forward_origin is a ForwardOrigin dataclass; serialise it.
        try:
            forward_origin = message.forward_origin.to_dict()
        except Exception:  # noqa: BLE001
            forward_origin = {"type": type(message.forward_origin).__name__}

    # Full update payload as a plain dict.
    try:
        raw: dict[str, Any] = message.to_dict()
    except Exception:  # noqa: BLE001
        raw = {}

    return {
        "platform": "telegram",
        "chat_id": str(message.chat_id),
        "message_id": message.message_id,
        "sender_id": sender_id,
        "sender_username": sender_username,
        "text": message.text or message.caption or "",
        "published_at": published_at,
        "received_at": _iso_now(),
        "is_forwarded": is_forwarded,
        "forward_origin": forward_origin,
        "raw": raw,
    }


# ---------------------------------------------------------------------------
# Public connector
# ---------------------------------------------------------------------------

class TelegramListenerConnector:
    """
    Manages the lifecycle of a Telegram bot that listens to all messages in
    the configured channels/groups and enqueues them for downstream processing.

    Parameters
    ----------
    queue:
        Shared asyncio.Queue onto which normalised message dicts are pushed.

    Usage
    -----
    ::

        queue: asyncio.Queue = asyncio.Queue()
        connector = TelegramListenerConnector(queue)
        await connector.start()
        ...
        await connector.stop()
    """

    def __init__(
        self,
        queue: asyncio.Queue[dict[str, Any]],
        app: Optional[Application] = None,
    ) -> None:
        self._queue = queue
        self._app: Application | None = app  # type: ignore[type-arg]
        self._poll_task: asyncio.Task[None] | None = None
        self._shared_app: bool = app is not None
        if self._shared_app and self._app is not None:
            self._app.add_handler(
                MessageHandler(
                    filters.ALL,
                    self._handle_message,
                )
            )

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """
        Initialise the PTB Application, register handlers, and begin polling.

        The method returns promptly; polling runs in a background task.
        """
        if self._shared_app:
            logger.info("telegram_listener.sharing_app_polling")
            return

        if self._app is not None:
            logger.warning("telegram_listener.already_running")
            return

        settings = get_settings()
        bot_token: str = settings.telegram_bot_token

        self._app = (
            Application.builder()
            .token(bot_token)
            .build()
        )

        # Register a catch-all handler for every text and caption message.
        self._app.add_handler(
            MessageHandler(
                filters.ALL,
                self._handle_message,
            )
        )

        # Register a global error handler so PTB never crashes on exceptions.
        self._app.add_error_handler(self._handle_error)

        logger.info("telegram_listener.starting")

        # Initialise and start the application (sets up the bot, job queue, etc.)
        await self._app.initialize()
        await self._app.start()

        # Begin polling in a background task so we don't block the caller.
        self._poll_task = asyncio.create_task(self._polling_loop())

    async def stop(self) -> None:
        """Gracefully stop polling and shut down the PTB Application."""
        if self._shared_app:
            return

        if self._poll_task is not None and not self._poll_task.done():
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass

        if self._app is not None:
            try:
                await self._app.updater.stop()  # type: ignore[union-attr]
            except Exception as exc:  # noqa: BLE001
                logger.warning("telegram_listener.updater_stop_error", exc_info=exc)
            try:
                await self._app.stop()
                await self._app.shutdown()
            except Exception as exc:  # noqa: BLE001
                logger.warning("telegram_listener.app_stop_error", exc_info=exc)

        self._app = None
        logger.info("telegram_listener.stopped")

    # ------------------------------------------------------------------
    # PTB handlers
    # ------------------------------------------------------------------

    async def _handle_message(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """
        PTB MessageHandler callback.

        Converts the incoming message into a normalised dict and enqueues it.
        """
        message: Optional[Message] = update.message or update.channel_post

        if message is None:
            # Edited messages, polls, etc. — skip for now.
            return

        try:
            payload = _message_to_dict(message)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "telegram_listener.serialise_error",
                exc_info=exc,
                update_id=update.update_id,
            )
            return

        await self._queue.put(payload)
        logger.debug(
            "telegram_listener.message_enqueued",
            chat_id=payload["chat_id"],
            message_id=payload["message_id"],
            is_forwarded=payload["is_forwarded"],
        )

    async def _handle_error(
        self,
        update: object,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """
        PTB global error handler — logs the error without crashing the bot.
        """
        logger.error(
            "telegram_listener.ptb_error",
            exc_info=context.error,
            update=str(update),
        )

    # ------------------------------------------------------------------
    # Polling loop
    # ------------------------------------------------------------------

    async def _polling_loop(self) -> None:
        """
        Run PTB's updater in polling mode inside the background task.

        Errors are caught and logged so the task stays alive through transient
        network failures.
        """
        assert self._app is not None  # noqa: S101

        try:
            # start_polling is non-blocking — the updater runs its own threads.
            await self._app.updater.start_polling(  # type: ignore[union-attr]
                allowed_updates=Update.ALL_TYPES,
                drop_pending_updates=True,
            )
            logger.info("telegram_listener.polling_started")

            # Keep the task alive until cancelled.
            while True:
                await asyncio.sleep(1)

        except asyncio.CancelledError:
            logger.info("telegram_listener.polling_cancelled")
            raise
        except Exception as exc:  # noqa: BLE001
            logger.error("telegram_listener.polling_loop_error", exc_info=exc)
