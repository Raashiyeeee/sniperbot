"""
tests/test_status_lifecycle.py
──────────────────────────────
Tests for Spec v3.1 §6.2 status lifecycle transitions and permissions.
"""
from __future__ import annotations

import pytest

from database.models import ProjectStatus
from projects.status_lifecycle import (
    assert_valid_transition,
    can_buy,
    is_terminal,
    is_valid_transition,
    requires_operator_action,
)


class TestStatusLifecycle:
    def test_pending_approval_transitions(self):
        # Pending approval can move to Approved, Rejected, or Expired
        assert is_valid_transition(ProjectStatus.PENDING_APPROVAL, ProjectStatus.APPROVED)
        assert is_valid_transition(ProjectStatus.PENDING_APPROVAL, ProjectStatus.REJECTED)
        assert is_valid_transition(ProjectStatus.PENDING_APPROVAL, ProjectStatus.EXPIRED)

        # Cannot jump straight to Buying or Completed
        assert not is_valid_transition(ProjectStatus.PENDING_APPROVAL, ProjectStatus.BUYING)
        assert not is_valid_transition(ProjectStatus.PENDING_APPROVAL, ProjectStatus.COMPLETED)
        assert not is_valid_transition(ProjectStatus.PENDING_APPROVAL, ProjectStatus.CHECKS_PASSED)

    def test_approved_transitions(self):
        # Approved can move to CA_DETECTED, EXPIRED, CANCELLED
        assert is_valid_transition(ProjectStatus.APPROVED, ProjectStatus.CA_DETECTED)
        assert is_valid_transition(ProjectStatus.APPROVED, ProjectStatus.EXPIRED)
        assert is_valid_transition(ProjectStatus.APPROVED, ProjectStatus.CANCELLED)

        # Cannot buy directly from Approved (must wait for CA and checks)
        assert not is_valid_transition(ProjectStatus.APPROVED, ProjectStatus.BUYING)
        assert not can_buy(ProjectStatus.APPROVED)

    def test_can_buy_only_on_checks_passed(self):
        # Spec v3.1 §6.2 table: Only CHECKS_PASSED has Can it buy? == Yes
        assert can_buy(ProjectStatus.CHECKS_PASSED)
        assert not can_buy(ProjectStatus.DETECTED)
        assert not can_buy(ProjectStatus.PENDING_APPROVAL)
        assert not can_buy(ProjectStatus.APPROVED)
        assert not can_buy(ProjectStatus.CA_DETECTED)
        assert not can_buy(ProjectStatus.BUYING)
        assert not can_buy(ProjectStatus.PURCHASED)
        assert not can_buy(ProjectStatus.REJECTED)
        assert not can_buy(ProjectStatus.SAFETY_BLOCKED)
        assert not can_buy(ProjectStatus.EXPIRED)
        assert not can_buy(ProjectStatus.CANCELLED)
        assert not can_buy(ProjectStatus.COMPLETED)

    def test_rejection_and_unblock(self):
        # Rejected can only move back to PENDING_APPROVAL on explicit unblock
        assert is_valid_transition(ProjectStatus.REJECTED, ProjectStatus.PENDING_APPROVAL)
        assert not is_valid_transition(ProjectStatus.REJECTED, ProjectStatus.APPROVED)
        assert not is_valid_transition(ProjectStatus.REJECTED, ProjectStatus.BUYING)

    def test_assert_valid_transition_raises(self):
        with pytest.raises(ValueError, match="Illegal status transition"):
            assert_valid_transition(
                ProjectStatus.PENDING_APPROVAL,
                ProjectStatus.BUYING,
                project_id="test-proj-123",
            )

    def test_terminal_states(self):
        assert is_terminal(ProjectStatus.COMPLETED)
        assert is_terminal(ProjectStatus.CANCELLED)
        assert is_terminal(ProjectStatus.EXPIRED)
        assert not is_terminal(ProjectStatus.PENDING_APPROVAL)
        assert not is_terminal(ProjectStatus.APPROVED)

    def test_operator_action_required(self):
        assert requires_operator_action(ProjectStatus.PENDING_APPROVAL)
        assert not requires_operator_action(ProjectStatus.APPROVED)
        assert not requires_operator_action(ProjectStatus.CHECKS_PASSED)

