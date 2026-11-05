"""
approval/approval_service.py
────────────────────────────
Business logic for approving, rejecting, and unblocking projects (Spec v3.1 §6).
Enforces status transitions, blacklist creation, and append-only audit logs.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import (
    Approval,
    ApprovalDecision,
    AuditEvent,
    BlacklistEntry,
    Project,
    ProjectStatus,
)
from projects.status_lifecycle import assert_valid_transition

logger = structlog.get_logger(__name__)


class ApprovalService:
    """Handles operator approval, rejection, and unblocking workflows."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def approve(
        self,
        project_id: str,
        operator_id: str,
        approved_source_ids: Optional[List[str]] = None,
        allowed_chains: Optional[List[str]] = None,
        reason: Optional[str] = None,
    ) -> Project:
        """
        Operator approves a project.
        Spec v3.1 §6:
        - Changes status to APPROVED
        - Authorizes monitoring of designated official CA sources
        - DOES NOT trigger an immediate buy
        """
        stmt = select(Project).where(Project.id == project_id)
        project = await self._session.scalar(stmt)
        if not project:
            raise ValueError(f"Project not found: {project_id}")

        assert_valid_transition(project.status, ProjectStatus.APPROVED, project.id)

        old_state = project.status
        project.status = ProjectStatus.APPROVED
        project.updated_at = datetime.now(timezone.utc)

        # Record Approval row
        approval = Approval(
            id=str(uuid.uuid4()),
            project_id=project.id,
            operator_id=str(operator_id),
            decision=ApprovalDecision.APPROVED,
            reason=reason,
            approved_source_ids={"sources": approved_source_ids or []},
            allowed_chains={"chains": allowed_chains or [project.chain_id] if project.chain_id else []},
            decided_at=datetime.now(timezone.utc),
        )
        self._session.add(approval)

        # Append-only audit
        audit = AuditEvent(
            actor=f"operator:{operator_id}",
            action="project_approved",
            entity_type="project",
            entity_id=project.id,
            old_state=old_state,
            new_state=ProjectStatus.APPROVED,
            reason=reason or "Operator approved monitoring",
        )
        self._session.add(audit)

        logger.info(
            "project_approved",
            project_id=project.id,
            operator_id=operator_id,
            allowed_chains=allowed_chains,
        )
        return project

    async def reject(
        self,
        project_id: str,
        operator_id: str,
        reason: Optional[str] = "Operator rejected",
    ) -> Project:
        """
        Operator rejects a project.
        Spec v3.1 §6 & §13:
        - Changes status to REJECTED
        - Adds to persistent BlacklistEntry
        - Blocks all future buy triggers
        """
        stmt = select(Project).where(Project.id == project_id)
        project = await self._session.scalar(stmt)
        if not project:
            raise ValueError(f"Project not found: {project_id}")

        assert_valid_transition(project.status, ProjectStatus.REJECTED, project.id)

        old_state = project.status
        project.status = ProjectStatus.REJECTED
        project.is_blacklisted = True
        project.updated_at = datetime.now(timezone.utc)

        # Record BlacklistEntry
        bl_stmt = select(BlacklistEntry).where(BlacklistEntry.project_id == project.id)
        existing_bl = await self._session.scalar(bl_stmt)
        if not existing_bl:
            bl_entry = BlacklistEntry(
                id=str(uuid.uuid4()),
                project_id=project.id,
                reason=reason,
                created_by=str(operator_id),
                is_unblocked=False,
                created_at=datetime.now(timezone.utc),
            )
            self._session.add(bl_entry)
        else:
            existing_bl.is_unblocked = False
            existing_bl.reason = reason

        # Record Approval row as REJECTED
        approval = Approval(
            id=str(uuid.uuid4()),
            project_id=project.id,
            operator_id=str(operator_id),
            decision=ApprovalDecision.REJECTED,
            reason=reason,
            decided_at=datetime.now(timezone.utc),
        )
        self._session.add(approval)

        # Append-only audit
        audit = AuditEvent(
            actor=f"operator:{operator_id}",
            action="project_rejected_blacklisted",
            entity_type="project",
            entity_id=project.id,
            old_state=old_state,
            new_state=ProjectStatus.REJECTED,
            reason=reason,
        )
        self._session.add(audit)

        logger.info(
            "project_rejected_blacklisted",
            project_id=project.id,
            operator_id=operator_id,
            reason=reason,
        )
        return project

    async def unblock(
        self,
        project_id: str,
        operator_id: str,
        reason: str,
    ) -> Project:
        """
        Explicit operator unblock of a blacklisted project.
        Spec v3.1 §6 & §13:
        - Requires explicit operator action and audit reason
        - Moves project back to PENDING_APPROVAL (does NOT auto-approve)
        """
        if not reason or not reason.strip():
            raise ValueError("Unblocking requires an explicit reason in audit trail")

        stmt = select(Project).where(Project.id == project_id)
        project = await self._session.scalar(stmt)
        if not project:
            raise ValueError(f"Project not found: {project_id}")

        assert_valid_transition(project.status, ProjectStatus.PENDING_APPROVAL, project.id)

        old_state = project.status
        project.status = ProjectStatus.PENDING_APPROVAL
        project.is_blacklisted = False
        project.updated_at = datetime.now(timezone.utc)

        # Mark BlacklistEntry unblocked
        bl_stmt = select(BlacklistEntry).where(BlacklistEntry.project_id == project.id)
        bl_entry = await self._session.scalar(bl_stmt)
        if bl_entry:
            bl_entry.is_unblocked = True
            bl_entry.unblocked_by = str(operator_id)
            bl_entry.unblocked_at = datetime.now(timezone.utc)
            bl_entry.unblock_reason = reason

        # Append-only audit
        audit = AuditEvent(
            actor=f"operator:{operator_id}",
            action="project_unblocked",
            entity_type="project",
            entity_id=project.id,
            old_state=old_state,
            new_state=ProjectStatus.PENDING_APPROVAL,
            reason=reason,
        )
        self._session.add(audit)

        logger.info(
            "project_unblocked",
            project_id=project.id,
            operator_id=operator_id,
            reason=reason,
        )
        return project
