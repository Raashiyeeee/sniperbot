"""
projects/status_lifecycle.py
─────────────────────────────
Enforces the formal project status lifecycle from Spec v3.1 §6.2.
All status transitions go through this module — never update status directly.
"""
from __future__ import annotations

from typing import Optional, Set

import structlog

from database.models import ProjectStatus

logger = structlog.get_logger(__name__)

# ── Valid transitions map ─────────────────────────────────────────────────────
# Maps current status → set of statuses it may legally move to

VALID_TRANSITIONS: dict[ProjectStatus, Set[ProjectStatus]] = {
    ProjectStatus.DETECTED: {
        ProjectStatus.PENDING_APPROVAL,
        ProjectStatus.EXPIRED,
    },
    ProjectStatus.PENDING_APPROVAL: {
        ProjectStatus.APPROVED,
        ProjectStatus.REJECTED,
        ProjectStatus.EXPIRED,
    },
    ProjectStatus.APPROVED: {
        ProjectStatus.CA_DETECTED,
        ProjectStatus.EXPIRED,
        ProjectStatus.CANCELLED,
    },
    ProjectStatus.REJECTED: {
        ProjectStatus.PENDING_APPROVAL,   # after explicit unblock
    },
    ProjectStatus.CA_DETECTED: {
        ProjectStatus.CHECKS_PASSED,
        ProjectStatus.SAFETY_BLOCKED,
        ProjectStatus.CANCELLED,
        ProjectStatus.EXPIRED,
    },
    ProjectStatus.CHECKS_PASSED: {
        ProjectStatus.BUYING,
        ProjectStatus.SAFETY_BLOCKED,
        ProjectStatus.CANCELLED,
    },
    ProjectStatus.BUYING: {
        ProjectStatus.PURCHASED,
        ProjectStatus.SAFETY_BLOCKED,
        ProjectStatus.CANCELLED,
    },
    ProjectStatus.PURCHASED: {
        ProjectStatus.COMPLETED,
    },
    # Terminal states — no further transitions (except explicit unblock from REJECTED)
    ProjectStatus.COMPLETED:      set(),
    ProjectStatus.SAFETY_BLOCKED: {
        ProjectStatus.CA_DETECTED,    # re-check after issue resolved
        ProjectStatus.CANCELLED,
        ProjectStatus.EXPIRED,
    },
    ProjectStatus.CANCELLED: set(),
    ProjectStatus.EXPIRED:   set(),
}


def is_valid_transition(
    current: ProjectStatus,
    target: ProjectStatus,
) -> bool:
    """Return True if moving from current → target is a legal transition."""
    allowed = VALID_TRANSITIONS.get(current, set())
    return target in allowed


def assert_valid_transition(
    current: ProjectStatus,
    target: ProjectStatus,
    project_id: str,
) -> None:
    """Raise ValueError if the transition is not permitted."""
    if not is_valid_transition(current, target):
        msg = (
            f"Illegal status transition for project {project_id}: "
            f"{current} → {target}"
        )
        logger.error("illegal_status_transition", project_id=project_id,
                     current=current, target=target)
        raise ValueError(msg)


def can_buy(status: ProjectStatus) -> bool:
    """Return True only for the status that authorises immediate buy submission."""
    return status == ProjectStatus.CHECKS_PASSED


def is_terminal(status: ProjectStatus) -> bool:
    """Return True if the status has no further outbound transitions."""
    return len(VALID_TRANSITIONS.get(status, set())) == 0


def requires_operator_action(status: ProjectStatus) -> bool:
    """Return True if the project is waiting for the operator to do something."""
    return status == ProjectStatus.PENDING_APPROVAL

