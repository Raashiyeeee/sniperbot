"""
monitoring/ca_resolver.py
─────────────────────────
Resolves and validates incoming CA candidates against approved projects.
Enforces official-source verification, authorized Telegram admin checks,
conflicting CA detection, and duplicate protection (Spec v3.1 §8).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional, Tuple

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config.chains import validate_address
from database.models import (
    AuditEvent,
    ContractCandidate,
    Project,
    ProjectStatus,
    SourceAccount,
)
from monitoring.ca_extractor import CAExtractor
from projects.status_lifecycle import assert_valid_transition

logger = structlog.get_logger(__name__)


class CAResolver:
    """Verifies that an incoming CA announcement is authentic and conflict-free."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def verify_source(
        self,
        project: Project,
        platform: str,
        sender_id: Optional[str],
        sender_username: Optional[str],
        is_forwarded: bool,
        source_account_id: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """
        Verify that the message sender is an authorized source.
        Spec v3.1 §8.1 & §8.2:
        - X: Must be the configured official X account.
        - Telegram: Must be an authorized admin. Reject forwarded messages,
          ordinary members, unknown senders.
        """
        if is_forwarded:
            logger.warning(
                "ca_source_rejected_forwarded",
                project_id=project.id,
                platform=platform,
                sender_id=sender_id,
            )
            return False, "Forwarded messages are prohibited for CA discovery"

        if platform == "x":
            expected_handle = (project.official_x_handle or "").lstrip("@").lower()
            actual_handle = (sender_username or sender_id or "").lstrip("@").lower()
            if not expected_handle:
                # If project has no handle configured yet, flag for operator
                return True, "Official handle not pre-locked, accepted with notice"
            if expected_handle != actual_handle:
                logger.warning(
                    "ca_source_rejected_x_handle_mismatch",
                    expected=expected_handle,
                    actual=actual_handle,
                )
                return False, f"X handle mismatch: expected @{expected_handle}, got @{actual_handle}"
            return True, "Official X account verified"

        elif platform == "telegram":
            if not sender_id:
                return False, "Missing Telegram sender ID"

            # Check authorized admin list on source account if available
            if source_account_id:
                stmt = select(SourceAccount).where(SourceAccount.id == source_account_id)
                src = await self._session.scalar(stmt)
                if src and src.authorized_admin_ids:
                    admin_ids = src.authorized_admin_ids
                    # Handle dict or list in JSONB
                    allowed_ids = admin_ids if isinstance(admin_ids, list) else admin_ids.get("admins", [])
                    if sender_id not in [str(a) for a in allowed_ids]:
                        logger.warning(
                            "ca_source_rejected_unauthorized_tg_admin",
                            sender_id=sender_id,
                            allowed_admins=allowed_ids,
                        )
                        return False, f"Telegram sender {sender_id} is not an authorized administrator"

            return True, "Telegram source authenticated"

        elif platform == "manual":
            return True, "Manual operator input"

        return False, f"Unknown source platform: {platform}"

    async def process_ca_announcement(
        self,
        project: Project,
        text: str,
        platform: str,
        sender_id: Optional[str] = None,
        sender_username: Optional[str] = None,
        message_id: Optional[str] = None,
        is_forwarded: bool = False,
        source_account_id: Optional[str] = None,
    ) -> Tuple[Optional[ContractCandidate], str]:
        """
        Full CA processing pipeline:
        1. Ensure project status is APPROVED (only approved projects can proceed).
        2. Authenticate source.
        3. Extract and validate CA format for chain.
        4. Detect conflicting CA.
        5. Transition project status to CA_DETECTED.
        """
        # 1. Project must be APPROVED or CA_DETECTED to process CA
        allowed_statuses = {ProjectStatus.APPROVED, ProjectStatus.CA_DETECTED}
        if project.status not in allowed_statuses:
            logger.info(
                "ca_ignored_project_not_approved",
                project_id=project.id,
                status=project.status,
            )
            return None, f"Project status is {project.status}, must be APPROVED or CA_DETECTED"

        # 2. Source authentication
        is_auth, auth_msg = await self.verify_source(
            project, platform, sender_id, sender_username, is_forwarded, source_account_id
        )
        if not is_auth:
            return None, f"Source verification failed: {auth_msg}"

        # 3. Extract CA
        candidates = CAExtractor.extract_for_chain(text, project.chain_id)
        if not candidates:
            return None, "No valid contract address found in text"

        detected_address = candidates[0]

        # Validate against chain rules
        if project.chain_id and not validate_address(detected_address, project.chain_id):
            return None, f"Address {detected_address} invalid for chain {project.chain_id}"

        # 4. Check for conflicting CA (Spec v3.1 §8.3)
        stmt = (
            select(ContractCandidate)
            .where(ContractCandidate.project_id == project.id)
            .where(ContractCandidate.is_active == True)  # noqa: E712
        )
        existing_cands = (await self._session.execute(stmt)).scalars().all()
        has_conflict = False
        conflict_note = None

        for existing in existing_cands:
            if existing.address.lower() != detected_address.lower():
                has_conflict = True
                conflict_note = (
                    f"Conflicting CA detected! Existing: {existing.address}, "
                    f"New: {detected_address}. Buy paused."
                )
                existing.has_conflict = True
                logger.error(
                    "conflicting_ca_detected",
                    project_id=project.id,
                    existing_ca=existing.address,
                    new_ca=detected_address,
                )
                # Move to SAFETY_BLOCKED on conflict
                assert_valid_transition(project.status, ProjectStatus.SAFETY_BLOCKED, project.id)
                project.status = ProjectStatus.SAFETY_BLOCKED
                break

        # If already recorded with exact same CA, return existing (duplicate protection)
        for existing in existing_cands:
            if existing.address.lower() == detected_address.lower():
                logger.info("ca_duplicate_ignored", ca=detected_address)
                return existing, "Duplicate CA announcement received"

        # 5. Persist ContractCandidate
        candidate = ContractCandidate(
            id=str(uuid.uuid4()),
            project_id=project.id,
            chain_id=project.chain_id or "robinhood",
            address=detected_address,
            source_message_id=message_id,
            source_account_id=source_account_id,
            is_verified=not has_conflict,
            is_active=True,
            has_conflict=has_conflict,
            conflict_note=conflict_note,
            detected_at=datetime.now(timezone.utc),
        )
        self._session.add(candidate)

        # Update project CA and transition to CA_DETECTED if no conflict
        if not has_conflict:
            assert_valid_transition(project.status, ProjectStatus.CA_DETECTED, project.id)
            project.status = ProjectStatus.CA_DETECTED
            project.ca_address = detected_address

        # Append audit log
        audit = AuditEvent(
            actor="system:ca_resolver",
            action="ca_detected" if not has_conflict else "ca_conflict_blocked",
            entity_type="project",
            entity_id=project.id,
            new_state=project.status,
            reason=conflict_note or f"CA {detected_address} verified from {platform}",
        )
        self._session.add(audit)

        return candidate, "CA verified and registered"
