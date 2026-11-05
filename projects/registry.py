"""
projects/registry.py
Registers, deduplicates and manages :class:`~database.models.Project` rows.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import List, Optional, Tuple

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import (
    Announcement,
    AuditEvent,
    EventType,
    Project,
    ProjectStatus,
)
from monitoring.announcement_parser import ParsedAnnouncement

logger = structlog.get_logger(__name__)


class ProjectRegistry:
    """Persist and query :class:`~database.models.Project` records.

    All database I/O uses SQLAlchemy 2.0 async style (``select()``,
    ``session.execute()``, ``session.scalar()``).

    Parameters
    ----------
    session:
        An open :class:`~sqlalchemy.ext.asyncio.AsyncSession`.
        The caller owns the session lifecycle (commit / rollback / close).
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @property
    def _s(self) -> AsyncSession:
        return self._session

    async def _find_by_ca(self, ca_address: str) -> Optional[Project]:
        """Return a Project whose contract address matches *ca_address*."""
        stmt = select(Project).where(Project.ca_address == ca_address).limit(1)
        return await self._s.scalar(stmt)

    async def _find_by_ticker_and_chain(
        self, ticker: str, chain_id: str
    ) -> Optional[Project]:
        """Return a Project matched by (ticker, chain_id) pair."""
        stmt = (
            select(Project)
            .where(Project.ticker == ticker.upper())
            .where(Project.chain_id == chain_id)
            .limit(1)
        )
        return await self._s.scalar(stmt)

    async def _find_by_x_handle(self, handle: str) -> Optional[Project]:
        """Return a Project whose official X handle matches *handle*."""
        stmt = (
            select(Project)
            .where(Project.official_x_handle == handle.lstrip("@").lower())
            .limit(1)
        )
        return await self._s.scalar(stmt)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_or_create_project(
        self, parsed: ParsedAnnouncement
    ) -> Tuple[Project, bool]:
        """Find an existing project or create a new one from *parsed*.

        Deduplication is attempted in this order:

        1. Contract address (``ca_address``) — most specific
        2. ``(ticker, chain_id)`` pair
        3. Official X handle (``author_id`` for X platform messages)

        If a matching project is found and is **blacklisted**, the existing
        project is returned unchanged (``created=False``).

        New projects are created with status
        :attr:`~database.models.ProjectStatus.PENDING_APPROVAL`.

        Parameters
        ----------
        parsed:
            A :class:`~monitoring.announcement_parser.ParsedAnnouncement`
            produced by :class:`~monitoring.announcement_parser.AnnouncementParser`.

        Returns
        -------
        ``(project, created)``
            *created* is ``True`` when a new row was inserted.
        """
        log = logger.bind(
            ticker=parsed.ticker,
            chain_id=parsed.chain_id,
            ca_address=parsed.ca_address,
        )

        existing: Optional[Project] = None

        # 1. Match by CA address
        if parsed.ca_address:
            existing = await self._find_by_ca(parsed.ca_address)
            if existing:
                log.info("project_registry.deduplicated", method="ca_address", project_id=str(existing.id))

        # 2. Match by ticker + chain
        if existing is None and parsed.ticker and parsed.chain_id:
            existing = await self._find_by_ticker_and_chain(parsed.ticker, parsed.chain_id)
            if existing:
                log.info("project_registry.deduplicated", method="ticker_chain", project_id=str(existing.id))

        # 3. Match by official X handle
        if existing is None and parsed.platform == "x" and parsed.author_id:
            existing = await self._find_by_x_handle(parsed.author_id)
            if existing:
                log.info("project_registry.deduplicated", method="x_handle", project_id=str(existing.id))

        # If a duplicate was found, return it (even if blacklisted — do NOT recreate)
        if existing is not None:
            return existing, False

        # --- Create new project ---
        project = Project(
            id=str(uuid.uuid4()),
            ticker=parsed.ticker.upper() if parsed.ticker else None,
            project_name=parsed.project_name,
            chain_id=parsed.chain_id,
            ca_address=parsed.ca_address,
            official_x_handle=(
                parsed.author_id.lstrip("@").lower()
                if parsed.platform == "x" and parsed.author_id
                else None
            ),
            expected_ca_time_str=getattr(parsed, "expected_ca_time_str", "Not specified"),
            launch_time_str=getattr(parsed, "launch_time_str", "Not specified"),
            network=getattr(parsed, "network", "Not specified"),
            status=ProjectStatus.PENDING_APPROVAL,
            description=parsed.description,
            expected_launch_time=parsed.expected_launch_time,
            created_at=datetime.now(tz=timezone.utc),
            updated_at=datetime.now(tz=timezone.utc),
        )
        self._s.add(project)
        await self._s.flush()  # assign DB-generated fields without committing

        log.info("project_registry.created", project_id=project.id, ticker=project.ticker)
        return project, True

    def apply_ai_intelligence(
        self,
        project: Project,
        ai_result: Any,
    ) -> None:
        """Enrich Project model with validated Gemini AI intelligence."""
        if ai_result is None:
            return

        proj_info = getattr(ai_result, "project", None)
        qa = getattr(ai_result, "quality_assessment", None)
        ver = getattr(ai_result, "verification", None)

        if proj_info:
            category_val = getattr(proj_info.primary_category, "value", str(proj_info.primary_category))
            project.category = category_val
            project.categories = getattr(proj_info, "categories", [category_val])
            if proj_info.website_url and not project.website_url:
                project.website_url = proj_info.website_url
            if proj_info.official_telegram and not project.official_telegram:
                project.official_telegram = proj_info.official_telegram
            if proj_info.founder_team:
                project.founder_team_info = proj_info.founder_team
            if proj_info.product_summary:
                project.product_summary = proj_info.product_summary
            if proj_info.documentation_url:
                project.documentation_url = proj_info.documentation_url

        if qa:
            project.ai_score = qa.overall_score
            project.score = qa.overall_score
            project.ai_scores_breakdown = qa.sub_scores
            project.ai_recommendation = getattr(qa.recommendation, "value", str(qa.recommendation))
            project.red_flags = qa.red_flags
            project.missing_information = qa.missing_information

        if ver:
            project.verification_flags = (
                ver.model_dump() if hasattr(ver, "model_dump") else ver
            )

        project.ai_evaluated_at = datetime.now(timezone.utc)

    async def create_announcement(
        self,
        project_id: str,
        parsed: ParsedAnnouncement,
        source_account_id: Optional[str] = None,
    ) -> Announcement:
        """Persist an :class:`~database.models.Announcement` linked to *project_id*.

        Parameters
        ----------
        project_id:
            UUID string of the owning :class:`~database.models.Project`.
        parsed:
            Structured announcement data.
        source_account_id:
            Optional FK to the monitored account that produced the message.

        Returns
        -------
        :class:`~database.models.Announcement`
            The newly created (unflushed) row.
        """
        announcement = Announcement(
            id=str(uuid.uuid4()),
            project_id=project_id,
            platform=parsed.platform,
            message_id=parsed.message_id,
            post_url=parsed.post_url,
            text=parsed.text,
            author_id=parsed.author_id,
            event_type=parsed.event_type,
            published_at=parsed.published_at,
            received_at=parsed.received_at,
            is_forwarded=parsed.is_forwarded,
            ca_address=parsed.ca_address,
            expected_launch_time=parsed.expected_launch_time,
            expected_ca_source=parsed.expected_ca_source,
            expected_ca_time_str=getattr(parsed, "expected_ca_time_str", "Not specified"),
            launch_time_str=getattr(parsed, "launch_time_str", "Not specified"),
            x_profile_url=getattr(parsed, "x_profile_url", None),
            network=getattr(parsed, "network", "Not specified"),
            source_account_id=source_account_id,
            raw=parsed.raw,
        )
        self._s.add(announcement)
        await self._s.flush()

        logger.info(
            "project_registry.announcement_created",
            announcement_id=announcement.id,
            project_id=project_id,
            platform=parsed.platform,
        )
        return announcement

    async def get_project(self, project_id: str) -> Optional[Project]:
        """Fetch a single project by its UUID string.

        Parameters
        ----------
        project_id:
            UUID string primary key.

        Returns
        -------
        :class:`~database.models.Project` or ``None`` if not found.
        """
        stmt = select(Project).where(Project.id == project_id)
        project = await self._s.scalar(stmt)
        if project is None:
            logger.warning("project_registry.not_found", project_id=project_id)
        return project

    async def list_projects(
        self,
        status: Optional[ProjectStatus] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Project]:
        """Return a paginated list of projects, optionally filtered by *status*.

        Parameters
        ----------
        status:
            If provided, only projects with this
            :class:`~database.models.ProjectStatus` are returned.
        limit:
            Maximum number of rows to return (default 50).
        offset:
            Row offset for pagination (default 0).

        Returns
        -------
        ``list[Project]``
        """
        stmt = select(Project).order_by(Project.created_at.desc())
        if status is not None:
            stmt = stmt.where(Project.status == status)
        stmt = stmt.limit(limit).offset(offset)

        result = await self._s.execute(stmt)
        projects = list(result.scalars().all())
        logger.debug(
            "project_registry.list",
            status=status,
            count=len(projects),
            limit=limit,
            offset=offset,
        )
        return projects

    async def update_project_status(
        self,
        project_id: str,
        new_status: ProjectStatus,
        actor: str,
        reason: Optional[str] = None,
    ) -> Project:
        """Transition a project to *new_status* and write an audit event.

        Parameters
        ----------
        project_id:
            UUID string of the target project.
        new_status:
            The desired :class:`~database.models.ProjectStatus`.
        actor:
            Human-readable identifier of who/what triggered the transition
            (e.g. ``"admin:alice"``, ``"bot:safety_check"``).
        reason:
            Optional free-text explanation appended to the audit row.

        Returns
        -------
        :class:`~database.models.Project`
            The updated project instance.

        Raises
        ------
        ValueError
            If no project with *project_id* exists.
        """
        log = logger.bind(project_id=project_id, new_status=new_status, actor=actor)

        project = await self.get_project(project_id)
        if project is None:
            raise ValueError(f"Project not found: {project_id}")

        previous_status = project.status
        project.status = new_status
        project.updated_at = datetime.now(tz=timezone.utc)

        # Append-only audit event
        audit = AuditEvent(
            id=str(uuid.uuid4()),
            project_id=project_id,
            actor=actor,
            action="status_change",
            previous_value=previous_status.value if previous_status else None,
            new_value=new_status.value,
            reason=reason,
            created_at=datetime.now(tz=timezone.utc),
        )
        self._s.add(audit)
        await self._s.flush()

        log.info(
            "project_registry.status_updated",
            previous_status=previous_status,
            audit_id=audit.id,
        )
        return project
