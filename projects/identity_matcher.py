"""
projects/identity_matcher.py
─────────────────────────────
Deduplicates projects by matching identity signals:
  - Ticker + chain
  - Contract address (CA)
  - Official X handle
  - Official Telegram channel

Prevents a duplicate X/Telegram announcement from creating a second project
record or bypassing the blacklist. (Spec v3.1 §8.4 and §13)
"""
from __future__ import annotations

from typing import Optional

import structlog
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Project, ProjectStatus

logger = structlog.get_logger(__name__)


class IdentityMatcher:
    """Match an incoming parsed announcement to an existing Project record."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find_existing(
        self,
        ticker: Optional[str] = None,
        chain_id: Optional[str] = None,
        ca_address: Optional[str] = None,
        official_x_handle: Optional[str] = None,
        official_telegram: Optional[str] = None,
    ) -> Optional[Project]:
        """
        Return the first Project that matches any of the supplied identity signals.
        Returns None if no match is found.
        """
        filters = []

        if ticker and chain_id:
            filters.append(
                (Project.ticker == ticker.upper()) &
                (Project.chain_id == chain_id)
            )

        if ca_address:
            # Check against direct ca_address column
            filters.append(
                func.lower(Project.ca_address) == ca_address.lower()
            )

        if official_x_handle:
            handle = official_x_handle.lstrip("@").lower()
            filters.append(func.lower(Project.official_x_handle) == handle)

        if official_telegram:
            tg = official_telegram.strip()
            tg_lower = tg.lstrip("@").lower()
            # Match username OR numeric chat id stored on the project
            filters.append(func.lower(Project.official_telegram) == tg_lower)
            filters.append(Project.official_telegram_chat_id == tg)
            filters.append(Project.official_telegram_chat_id == tg.lstrip("@"))

        if not filters:
            return None

        stmt = select(Project).where(or_(*filters)).limit(1)
        result = await self._session.execute(stmt)
        project = result.scalar_one_or_none()

        if project:
            logger.info(
                "identity_match_found",
                project_id=project.id,
                project_name=project.name,
                status=project.status,
                is_blacklisted=project.is_blacklisted,
            )

        return project

    async def is_blacklisted(
        self,
        ticker: Optional[str] = None,
        chain_id: Optional[str] = None,
        ca_address: Optional[str] = None,
        official_x_handle: Optional[str] = None,
        official_telegram: Optional[str] = None,
    ) -> bool:
        """
        Return True if an existing matching project is blacklisted.
        A blacklisted identity must not create a new project record.
        """
        project = await self.find_existing(
            ticker=ticker,
            chain_id=chain_id,
            ca_address=ca_address,
            official_x_handle=official_x_handle,
            official_telegram=official_telegram,
        )
        if project and project.is_blacklisted:
            logger.warning(
                "blacklisted_identity_detected",
                project_id=project.id,
                project_name=project.name,
            )
            return True
        return False

