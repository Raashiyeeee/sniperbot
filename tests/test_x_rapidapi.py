"""
tests/test_x_rapidapi.py
────────────────────────
Unit tests for XRapidAPIConnector parsing and enqueuing.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from connectors.x_rapidapi import XRapidAPIConnector


def test_rapidapi_payload_processing_and_deduplication():
    async def _test():
        queue: asyncio.Queue = asyncio.Queue()
        connector = XRapidAPIConnector(queue)

        mock_payload = {
            "timeline": [
                {
                    "tweet_id": "tweet_8888",
                    "text": "Official launch of $RAPID on Robinhood Chain!",
                    "user_info": {"screen_name": "RapidTeam"},
                    "created_at": "2026-11-04T12:00:00Z",
                },
                {
                    "tweet_id": "tweet_8888",  # duplicate, should be ignored
                    "text": "Official launch duplicate",
                },
                {
                    "tweet_id": "tweet_9999",
                    "text": "Contract Address 0x1111222233334444555566667777888899990000",
                    "user_info": {"screen_name": "RapidTeam"},
                    "created_at": "2026-11-04T12:00:05Z",
                },
            ]
        }

        connector._process_raw_response(mock_payload)

        # Queue should contain exactly 2 distinct messages
        assert queue.qsize() == 2

        msg1 = await queue.get()
        assert msg1["message_id"] == "tweet_8888"
        assert msg1["author_id"] == "RapidTeam"
        assert "Official launch" in msg1["text"]
        assert msg1["platform"] == "x"

        msg2 = await queue.get()
        assert msg2["message_id"] == "tweet_9999"
        assert "0x1111222233334444555566667777888899990000" in msg2["text"]

    asyncio.run(_test())


def test_ancient_tweets_and_replies_discarded():
    async def _test():
        queue: asyncio.Queue = asyncio.Queue()
        connector = XRapidAPIConnector(queue)

        payload = {
            "statuses": [
                {
                    # Tweet from 2023 (like Image 2) - MUST be discarded!
                    "id": "tweet_2023",
                    "text": "Old NFT yearbook CA soon!",
                    "created_at": "Fri Mar 10 20:32:00 +0000 2023",
                    "user_info": {"screen_name": "OldAccount"},
                },
                {
                    # Reply/comment - MUST be discarded!
                    "id": "tweet_reply",
                    "text": "Replying to someone: CA soon",
                    "in_reply_to_status_id": "12345",
                    "created_at": "2026-11-05T12:00:00Z",
                    "user_info": {"screen_name": "ReplyGuy"},
                },
                {
                    # Retweet - MUST be discarded!
                    "id": "tweet_rt",
                    "text": "RT @Dev: CA soon",
                    "retweeted_status": {"id": "111"},
                    "created_at": "2026-11-05T12:00:00Z",
                    "user_info": {"screen_name": "Retweeter"},
                },
                {
                    # Fresh original tweet from Nov 2026 - MUST be accepted!
                    "id": "tweet_fresh_2026",
                    "text": "Official launch CA soon!",
                    "created_at": "2026-11-05T12:00:00Z",
                    "user_info": {"screen_name": "FreshDev"},
                },
            ]
        }

        connector._process_raw_response(payload)

        # Only the fresh original 2026 tweet should be enqueued
        assert queue.qsize() == 1
        item = await queue.get()
        assert item["message_id"] == "tweet_fresh_2026"

    asyncio.run(_test())

