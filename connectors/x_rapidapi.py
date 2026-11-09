"""
connectors/x_rapidapi.py
────────────────────────
RapidAPI Twitter/X connector for token sniper bot.
Polls Twitter search queries and target account timelines via RapidAPI (twitter-api45).
Enqueues normalised message dicts onto the shared asyncio.Queue.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

from dateutil import parser as dateutil_parser
import httpx
import structlog

from config.settings import get_settings

logger = structlog.get_logger(__name__)


class XRapidAPIConnector:
    """
    RapidAPI-based polling connector for X (Twitter).
    Serves as an affordable, reliable alternative to the official X API Filtered Stream.
    """

    def __init__(
        self,
        queue: asyncio.Queue[Dict[str, Any]],
        session_factory: Optional[Any] = None,
    ) -> None:
        self._queue = queue
        self._session_factory = session_factory
        self._settings = get_settings()
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._seen_tweet_ids: Set[str] = set()
        self._client: Optional[httpx.AsyncClient] = None

    @property
    def is_configured(self) -> bool:
        return bool(self._settings.rapidapi_key)

    async def start(self) -> None:
        """Start polling RapidAPI for recent tweets."""
        if not self.is_configured:
            logger.warning("x_rapidapi.unconfigured", msg="No RAPIDAPI_KEY supplied; skipping X polling")
            return

        if self._running:
            return

        self._running = True
        self._client = httpx.AsyncClient(timeout=10.0)
        self._task = asyncio.create_task(self._poll_loop())
        logger.info("x_rapidapi.started", host=self._settings.rapidapi_twitter_host)

    async def stop(self) -> None:
        """Stop polling and close HTTP client."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        if self._client:
            await self._client.aclose()
            self._client = None
        logger.info("x_rapidapi.stopped")

    async def _get_approved_handles(self) -> List[str]:
        """Fetch official X handles for all projects in APPROVED or CA_DETECTED state."""
        if not self._session_factory:
            return []
        try:
            from sqlalchemy import select
            from database.models import Project, ProjectStatus

            async with self._session_factory() as session:
                stmt = select(Project.official_x_handle).where(
                    Project.status.in_([ProjectStatus.APPROVED, ProjectStatus.CA_DETECTED]),
                    Project.official_x_handle.isnot(None),
                )
                rows = (await session.execute(stmt)).scalars().all()
                handles = list({h.lstrip("@").strip() for h in rows if h and h.strip()})
                return handles
        except Exception as exc:
            logger.debug("x_rapidapi.get_approved_handles_error", error=str(exc))
            return []

    async def _poll_approved_handles_once(self) -> None:
        """High-priority poll of approved official X accounts only."""
        if not self._session_factory:
            return
        approved_handles = await self._get_approved_handles()
        for handle in approved_handles:
            if not self._running:
                break
            try:
                await self._fetch_search_query(f"from:{handle}")
            except Exception as exc:
                logger.warning("x_rapidapi.approved_handle_failed", handle=handle, error=str(exc))
            await asyncio.sleep(0.25)

    async def _poll_loop(self) -> None:
        """Continuous polling loop: discovery queries + fast approved-handle scans."""
        queries = [q.strip() for q in self._settings.rapidapi_search_queries.split(",") if q.strip()]
        if not queries:
            queries = ['"CA soon" OR "contract dropping" OR "contract dropping soon"']

        discovery_interval = max(5.0, float(self._settings.rapidapi_poll_interval_seconds))
        approved_interval = max(
            1.0,
            float(getattr(self._settings, "rapidapi_approved_poll_interval_seconds", 3.0)),
        )
        last_discovery = 0.0

        while self._running:
            now = asyncio.get_event_loop().time()

            # 1. Global discovery on a slower cadence
            if (now - last_discovery) >= discovery_interval:
                for query in queries:
                    if not self._running:
                        break
                    try:
                        await self._fetch_search_query(query)
                    except Exception as exc:
                        logger.warning("x_rapidapi.query_failed", query=query, error=str(exc))
                    await asyncio.sleep(1.0)
                last_discovery = asyncio.get_event_loop().time()
                logger.info(
                    "x_rapidapi.discovery_scan_completed",
                    next_discovery_in_seconds=discovery_interval,
                )

            # 2. Fast approved-account CA monitoring (target 1–5s)
            try:
                await self._poll_approved_handles_once()
            except Exception as exc:
                logger.warning("x_rapidapi.approved_scan_failed", error=str(exc))

            await asyncio.sleep(approved_interval)

    async def _fetch_search_query(self, query: str) -> None:
        """Search recent tweets for a specific keyword query."""
        if not self._client:
            return

        url = f"https://{self._settings.rapidapi_twitter_host}/search.php"
        headers = {
            "x-rapidapi-key": self._settings.rapidapi_key,
            "x-rapidapi-host": self._settings.rapidapi_twitter_host,
        }
        params = {
            "query": query,
            "search_type": "Latest",
        }

        try:
            resp = await self._client.get(url, headers=headers, params=params)
            if resp.status_code == 200:
                data = resp.json()
                self._process_raw_response(data)
            elif resp.status_code == 429:
                logger.warning("x_rapidapi.rate_limited", status=429)
                await asyncio.sleep(5.0)
            else:
                logger.warning("x_rapidapi.bad_status", status=resp.status_code)
        except (httpx.RequestError, Exception) as exc:
            logger.debug("x_rapidapi.fetch_error", error=str(exc))

    def _process_raw_response(self, data: Any) -> None:
        """Parse RapidAPI payload variants and enqueue unseen tweets."""
        items: List[Dict[str, Any]] = []

        if isinstance(data, dict):
            for key in ("timeline", "statuses", "tweets", "results", "data", "entries"):
                val = data.get(key)
                if isinstance(val, list):
                    items = val
                    break
        elif isinstance(data, list):
            items = data

        now_iso = datetime.now(timezone.utc).isoformat()

        # Parse minimum date cutoff (defaults to 2026-10-01) to discard ancient tweets
        min_dt: Optional[datetime] = None
        if self._settings.min_tweet_date:
            try:
                min_dt = dateutil_parser.parse(self._settings.min_tweet_date)
                if min_dt.tzinfo is None:
                    min_dt = min_dt.replace(tzinfo=timezone.utc)
            except Exception:
                pass

        for item in items:
            if not isinstance(item, dict):
                continue

            tweet_id = str(item.get("tweet_id") or item.get("id") or item.get("rest_id") or "")
            if not tweet_id or tweet_id in self._seen_tweet_ids:
                continue

            self._seen_tweet_ids.add(tweet_id)
            if len(self._seen_tweet_ids) > 10000:
                # Keep rolling set bounded
                self._seen_tweet_ids = set(list(self._seen_tweet_ids)[-5000:])

            legacy = item.get("legacy") if isinstance(item.get("legacy"), dict) else {}

            # 1. Ignore Retweets
            if item.get("retweeted_status") or legacy.get("retweeted_status") or item.get("retweeted"):
                continue

            # 2. Ignore Replies / Comments in other people's threads (not original announcements)
            if (
                item.get("in_reply_to_status_id")
                or legacy.get("in_reply_to_status_id")
                or item.get("in_reply_to_screen_name")
                or legacy.get("in_reply_to_screen_name")
            ):
                continue

            # 3. Date check: Discard tweets published before min_tweet_date (e.g. ancient 2023 tweets)
            published_raw = item.get("created_at") or legacy.get("created_at")
            if published_raw and min_dt:
                try:
                    clean_dt_str = str(published_raw).replace("·", " ").strip()
                    dt = dateutil_parser.parse(clean_dt_str)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    if dt < min_dt:
                        logger.debug("x_rapidapi.ancient_tweet_discarded", tweet_id=tweet_id, published_at=str(dt))
                        continue
                except Exception:
                    pass

            text = (
                item.get("text")
                or item.get("full_text")
                or item.get("desc")
                or legacy.get("full_text")
                or legacy.get("text")
                or ""
            )
            author_id = ""
            author_account_id = ""
            user_info = item.get("user_info") or item.get("user") or {}
            if isinstance(user_info, dict):
                author_id = str(user_info.get("screen_name") or user_info.get("username") or "")
                author_account_id = str(user_info.get("id") or user_info.get("rest_id") or user_info.get("id_str") or "")
                if not author_id and author_account_id:
                    author_id = author_account_id
            if not author_id and "core" in item:
                try:
                    user_res = item["core"].get("user_results", {}).get("result", {})
                    author_id = str(user_res.get("legacy", {}).get("screen_name") or "")
                    if not author_account_id:
                        author_account_id = str(user_res.get("rest_id") or user_res.get("id") or "")
                except Exception:
                    pass

            published_at = item.get("created_at") or legacy.get("created_at") or now_iso

            clean_author = author_id.lstrip("@")
            post_url = f"https://x.com/{clean_author}/status/{tweet_id}" if clean_author else f"https://x.com/i/web/status/{tweet_id}"
            profile_url = f"https://x.com/{clean_author}" if clean_author else None
            author_name = str(user_info.get("name") or "") if isinstance(user_info, dict) else ""
            author_bio = str(user_info.get("description") or user_info.get("bio") or "") if isinstance(user_info, dict) else ""

            payload: Dict[str, Any] = {
                "platform": "x",
                "message_id": tweet_id,
                "author_id": author_id,
                "author_account_id": author_account_id,
                "author_name": author_name,
                "author_bio": author_bio,
                "user_info": user_info,
                "text": text,
                "post_url": post_url,
                "profile_url": profile_url,
                "published_at": published_at,
                "received_at": now_iso,
                "raw": item,
            }

            self._queue.put_nowait(payload)
            logger.info("x_rapidapi.tweet_enqueued", tweet_id=tweet_id, author=author_id)

