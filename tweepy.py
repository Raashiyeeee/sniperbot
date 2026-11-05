"""
tweepy fallback shim.
"""
from __future__ import annotations
import sys
from typing import Any


class Tweet:
    def __init__(self, id: Any = "1", text: str = "", author_id: Any = None, data: Any = None, created_at: Any = None):
        self.id = id
        self.text = text
        self.author_id = author_id
        self.data = data or {}
        self.created_at = created_at


class _AsyncStreamingClient:
    def __init__(self, bearer_token: str = "", **kwargs: Any) -> None:
        self.bearer_token = bearer_token

    async def filter(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def disconnect(self) -> None:
        pass


class _AsynchronousModule:
    AsyncStreamingClient = _AsyncStreamingClient


asynchronous = _AsynchronousModule()
sys.modules["tweepy.asynchronous"] = asynchronous

