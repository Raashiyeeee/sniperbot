"""
connectors/x_stream.py
----------------------
X (Twitter) filtered-stream connector for the token sniper bot.

Uses tweepy v4 AsyncStreamingClient to monitor tweets for token-launch
intent phrases and enqueues normalised dicts onto a shared asyncio.Queue.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import structlog
import tweepy
import tweepy.asynchronous
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_never,
    wait_exponential,
)

from config.settings import get_settings

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Launch-intent filter phrases (tweepy rule value fragments)
# ---------------------------------------------------------------------------
_LAUNCH_PHRASES: list[str] = [
    "launching today",
    "launching tomorrow",
    "launch time",
    "CA soon",
    "contract dropping",
    "fair launch",
    "stealth launch",
    "token launches",
    "liquidity added",
    "trading live",
    "pool live",
    "CA dropping",
    "mint address",
]

# Build a single OR-joined rule (Twitter API max rule length is 512 chars for
# Basic access; split into multiple rules if the project grows).
_RULE_TAG = "token_sniper_launch_phrases"


def _build_rule_value() -> str:
    """Return a tweepy filter-rule value string joining all launch phrases."""
    # Wrap each phrase in quotes so multi-word phrases are treated as exact.
    quoted = [f'"{phrase}"' for phrase in _LAUNCH_PHRASES]
    return " OR ".join(quoted)


# ---------------------------------------------------------------------------
# Custom streaming listener
# ---------------------------------------------------------------------------
class _TokenLaunchStreamListener(tweepy.asynchronous.AsyncStreamingClient):
    """
    Internal AsyncStreamingClient subclass that pushes normalised tweet dicts
    onto the caller-supplied asyncio.Queue.
    """

    def __init__(self, bearer_token: str, queue: asyncio.Queue[dict[str, Any]], **kwargs: Any) -> None:
        super().__init__(bearer_token, **kwargs)
        self._queue = queue

    async def on_tweet(self, tweet: tweepy.Tweet) -> None:  # type: ignore[override]
        """Called by tweepy for every matching tweet; enqueues a normalised dict."""
        now_iso = datetime.now(timezone.utc).isoformat()
        published_at = ""
        if tweet.created_at is not None:
            published_at = tweet.created_at.isoformat()

        payload: dict[str, Any] = {
            "platform": "x",
            "message_id": str(tweet.id),
            "author_id": str(tweet.author_id) if tweet.author_id is not None else "",
            "text": tweet.text,
            "post_url": f"https://x.com/i/web/status/{tweet.id}",
            "published_at": published_at,
            "received_at": now_iso,
            "raw": tweet.data,
        }

        await self._queue.put(payload)
        logger.debug(
            "x_stream.tweet_enqueued",
            tweet_id=payload["message_id"],
            author_id=payload["author_id"],
        )

    async def on_errors(self, errors: dict[str, Any]) -> None:  # type: ignore[override]
        logger.error("x_stream.api_errors", errors=errors)

    async def on_exception(self, exception: Exception) -> None:  # type: ignore[override]
        logger.error("x_stream.exception", exc_info=exception)

    async def on_disconnect(self) -> None:  # type: ignore[override]
        logger.warning("x_stream.disconnected")

    async def on_connection_error(self) -> None:  # type: ignore[override]
        logger.warning("x_stream.connection_error")


# ---------------------------------------------------------------------------
# Public connector
# ---------------------------------------------------------------------------
class XStreamConnector:
    """
    Manages the lifecycle of an X (Twitter) filtered stream.

    Parameters
    ----------
    queue:
        Shared asyncio.Queue onto which normalised tweet dicts are pushed.

    Usage
    -----
    ::

        queue: asyncio.Queue = asyncio.Queue()
        connector = XStreamConnector(queue)
        await connector.start()
        ...
        await connector.stop()
    """

    def __init__(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._queue = queue
        self._stream: _TokenLaunchStreamListener | None = None
        self._stream_task: asyncio.Task[None] | None = None
        self._running = False

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """
        Set up filter rules and begin streaming.

        Retries with exponential backoff on transient errors so the connector
        self-heals after network blips or temporary API unavailability.
        """
        if self._running:
            logger.warning("x_stream.already_running")
            return

        settings = get_settings()
        bearer_token: str = settings.x_bearer_token

        self._stream = _TokenLaunchStreamListener(
            bearer_token=bearer_token,
            queue=self._queue,
            # Request fields we want back on every tweet.
            expansions=["author_id"],
            tweet_fields=["created_at", "author_id", "text", "id"],
            wait_on_rate_limit=True,
        )

        await self._sync_rules()

        self._running = True
        logger.info("x_stream.starting")
        self._stream_task = asyncio.create_task(self._run_with_retry())

    async def stop(self) -> None:
        """Gracefully disconnect the stream and cancel the background task."""
        self._running = False
        if self._stream is not None:
            self._stream.disconnect()
            logger.info("x_stream.disconnecting")

        if self._stream_task is not None and not self._stream_task.done():
            self._stream_task.cancel()
            try:
                await self._stream_task
            except asyncio.CancelledError:
                pass

        logger.info("x_stream.stopped")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _sync_rules(self) -> None:
        """
        Delete all existing stream rules and install the current launch-phrase
        rule so the stream always reflects the latest configuration.
        """
        assert self._stream is not None  # noqa: S101

        # Fetch existing rules.
        resp = await self._stream.get_rules()
        existing: list[tweepy.StreamRule] = resp.data or []
        if existing:
            ids = [rule.id for rule in existing]
            await self._stream.delete_rules(ids)
            logger.info("x_stream.rules_deleted", count=len(ids))

        # Add the new combined rule.
        rule_value = _build_rule_value()
        await self._stream.add_rules(tweepy.StreamRule(value=rule_value, tag=_RULE_TAG))
        logger.info("x_stream.rules_added", rule=rule_value)

    async def _run_with_retry(self) -> None:
        """
        Filter-stream loop with exponential-backoff retry via tenacity.

        Stops only when ``self._running`` is False (i.e. ``stop()`` was called).
        """
        async for attempt in AsyncRetrying(
            retry=retry_if_exception_type(Exception),
            wait=wait_exponential(multiplier=1, min=2, max=60),
            stop=stop_never,
            reraise=False,
        ):
            with attempt:
                if not self._running:
                    return
                logger.info(
                    "x_stream.connecting",
                    attempt=attempt.retry_state.attempt_number,
                )
                assert self._stream is not None  # noqa: S101
                # filter() is blocking-style but the async version runs the
                # event loop internally; tweepy handles reconnects internally
                # for HTTP errors; we add an outer retry for fatal exceptions.
                await self._stream.filter()

            if not self._running:
                return
            logger.warning(
                "x_stream.reconnecting",
                next_wait=attempt.retry_state.next_action,
            )
