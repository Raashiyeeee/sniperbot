"""
monitoring/approved_sources.py
──────────────────────────────
In-memory registry of approved project monitoring targets (X handles + Telegram chats).
Updated when operators set official accounts; used by Telegram listener scoping
and X approved-handle polling helpers.
"""
from __future__ import annotations

import threading
from typing import Dict, Optional, Set

import structlog

logger = structlog.get_logger(__name__)


class ApprovedSourcesRegistry:
    """Thread-safe cache of chats/handles that should be actively monitored."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        # chat_id -> project_id
        self._telegram_chats: Dict[str, str] = {}
        # handle (lowercase, no @) -> project_id
        self._x_handles: Dict[str, str] = {}

    def set_telegram_chat(self, chat_id: str, project_id: str) -> None:
        with self._lock:
            self._telegram_chats[str(chat_id)] = project_id
        logger.info("approved_sources.telegram_chat_registered", chat_id=chat_id, project_id=project_id)

    def clear_telegram_chat(self, chat_id: str) -> None:
        with self._lock:
            self._telegram_chats.pop(str(chat_id), None)

    def set_x_handle(self, handle: str, project_id: str) -> None:
        clean = handle.lstrip("@").lower()
        with self._lock:
            self._x_handles[clean] = project_id
        logger.info("approved_sources.x_handle_registered", handle=clean, project_id=project_id)

    def clear_x_handle(self, handle: str) -> None:
        clean = handle.lstrip("@").lower()
        with self._lock:
            self._x_handles.pop(clean, None)

    def is_allowed_telegram_chat(self, chat_id: str) -> bool:
        with self._lock:
            return str(chat_id) in self._telegram_chats

    def project_id_for_telegram_chat(self, chat_id: str) -> Optional[str]:
        with self._lock:
            return self._telegram_chats.get(str(chat_id))

    def allowed_telegram_chats(self) -> Set[str]:
        with self._lock:
            return set(self._telegram_chats.keys())

    def allowed_x_handles(self) -> Set[str]:
        with self._lock:
            return set(self._x_handles.keys())

    def replace_all(
        self,
        telegram_chats: Dict[str, str],
        x_handles: Dict[str, str],
    ) -> None:
        with self._lock:
            self._telegram_chats = {str(k): v for k, v in telegram_chats.items()}
            self._x_handles = {k.lstrip("@").lower(): v for k, v in x_handles.items()}
        logger.info(
            "approved_sources.reloaded",
            telegram_chats=len(self._telegram_chats),
            x_handles=len(self._x_handles),
        )


approved_sources = ApprovedSourcesRegistry()
