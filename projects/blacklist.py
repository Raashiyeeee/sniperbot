"""
projects/blacklist.py
─────────────────────
Persistent blacklist checker and query helper (Spec v3.1 §13).
Ensures rejected projects cannot be traded via duplicates or manual imports.
"""
from __future__ import annotations

import structlog
from typing import Optional
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import BlacklistEntry, Project

logger = structlog.get_logger(__name__)


class BlacklistService:
    """Provides blacklist checks across all entry points."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def is_blacklisted(
        self,
        project_id: Optional[str] = None,
        ca_address: Optional[str] = None,
        ticker: Optional[str] = None,
        chain_id: Optional[str] = None,
    ) -> bool:
        """
        Check if any matching identity exists on the persistent blacklist.
        Returns True if blocked.
        """
        if project_id:
            stmt = (
                select(BlacklistEntry)
                .where(BlacklistEntry.project_id == project_id)
                .where(BlacklistEntry.is_unblocked == False)  # noqa: E712
            )
            entry = await self._session.scalar(stmt)
            if entry:
                return True

        filters = []
        if ca_address:
            filters.append(Project.ca_address == ca_address)
        if ticker and chain_id:
            filters.append(
                (Project.ticker == ticker.upper()) & (Project.chain_id == chain_id)
            )

        if not filters:
            return False

        stmt = (
            select(Project)
            .join(BlacklistEntry, BlacklistEntry.project_id == Project.id)
            .where(BlacklistEntry.is_unblocked == False)  # noqa: E712
            .where(or_(*filters))
            .limit(1)
        )
        matched = await self._session.scalar(stmt)
        if matched:
            logger.warning(
                "blacklist_match_found",
                project_id=matched.id,
                ticker=matched.ticker,
                ca=ca_address,
            )
            return True

        return False
