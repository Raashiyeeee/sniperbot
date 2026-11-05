"""
ai/cache.py
───────────
Intelligent evaluation caching system.
Caches project evaluation results in-memory and in SQLite/PostgreSQL to prevent
duplicate Gemini API queries for the same project or repeated posts.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai.schemas import ProjectIntelligenceResult
from database.models import AIEvaluationCache

logger = structlog.get_logger(__name__)


def generate_project_cache_key(
    name: Optional[str] = None,
    handle: Optional[str] = None,
    ticker: Optional[str] = None,
    website: Optional[str] = None,
) -> str:
    """Generate a stable, normalized SHA-256 cache key for a project identity."""
    norm_name = (name or "").strip().lower()
    norm_handle = (handle or "").strip().lower().lstrip("@")
    norm_ticker = (ticker or "").strip().upper()
    norm_website = (website or "").strip().lower()

    identity_str = f"{norm_name}:{norm_handle}:{norm_ticker}:{norm_website}"
    return hashlib.sha256(identity_str.encode("utf-8")).hexdigest()


class EvaluationCacheManager:
    """Manages memory and database caching for Gemini evaluations."""

    def __init__(self, session_factory: Optional[Any] = None) -> None:
        self._session_factory = session_factory
        self._mem_cache: Dict[str, Dict[str, Any]] = {}

    def get_from_memory(self, cache_key: str) -> Optional[ProjectIntelligenceResult]:
        """Check fast in-memory cache."""
        entry = self._mem_cache.get(cache_key)
        if not entry:
            return None

        # Check expiration
        expires_at = entry.get("expires_at")
        if expires_at and datetime.now(timezone.utc) > expires_at:
            del self._mem_cache[cache_key]
            return None

        try:
            return ProjectIntelligenceResult.model_validate(entry["data"])
        except Exception:
            return None

    def store_in_memory(
        self,
        cache_key: str,
        result: ProjectIntelligenceResult,
        ttl_hours: int = 24,
    ) -> None:
        """Store evaluation in local memory."""
        self._mem_cache[cache_key] = {
            "data": result.model_dump(),
            "expires_at": datetime.now(timezone.utc) + timedelta(hours=ttl_hours),
        }

    async def get(
        self,
        session: AsyncSession,
        cache_key: str,
    ) -> Optional[ProjectIntelligenceResult]:
        """Fetch cached evaluation, checking memory first then database."""
        # 1. Fast memory check
        mem_res = self.get_from_memory(cache_key)
        if mem_res is not None:
            return mem_res

        # 2. Database check
        try:
            stmt = select(AIEvaluationCache).where(AIEvaluationCache.cache_key == cache_key).limit(1)
            row = (await session.execute(stmt)).scalar_one_or_none()
            if row:
                if row.expires_at and datetime.now(timezone.utc) > row.expires_at:
                    return None
                result = ProjectIntelligenceResult.model_validate(row.evaluation_data)
                # Populate memory cache
                self.store_in_memory(cache_key, result)
                return result
        except Exception as exc:
            logger.warning("cache_read_error", error=str(exc))

        return None

    async def store(
        self,
        session: AsyncSession,
        cache_key: str,
        result: ProjectIntelligenceResult,
        model_name: str = "gemini-2.0-flash",
        ttl_hours: int = 48,
    ) -> None:
        """Persist evaluation in memory and database."""
        self.store_in_memory(cache_key, result, ttl_hours=ttl_hours)

        try:
            now = datetime.now(timezone.utc)
            expires = now + timedelta(hours=ttl_hours)

            stmt = select(AIEvaluationCache).where(AIEvaluationCache.cache_key == cache_key).limit(1)
            existing = (await session.execute(stmt)).scalar_one_or_none()

            dump = result.model_dump()
            if existing:
                existing.evaluation_data = dump
                existing.overall_score = result.quality_assessment.overall_score
                existing.recommendation = result.quality_assessment.recommendation.value
                existing.model_used = model_name
                existing.expires_at = expires
            else:
                entry = AIEvaluationCache(
                    cache_key=cache_key,
                    project_name=result.project.name,
                    source_url=result.announcement_analysis.announcement_url,
                    evaluation_data=dump,
                    overall_score=result.quality_assessment.overall_score,
                    recommendation=result.quality_assessment.recommendation.value,
                    model_used=model_name,
                    created_at=now,
                    expires_at=expires,
                )
                session.add(entry)
            await session.flush()
        except Exception as exc:
            logger.warning("cache_write_error", error=str(exc))
