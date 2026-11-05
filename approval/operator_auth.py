"""
approval/operator_auth.py
─────────────────────────
Operator authentication and authorization for all trading, approval,
and emergency-stop actions (Spec v3.1 §14.3).
"""
from __future__ import annotations

import structlog
from typing import Optional, Set

from config.settings import get_settings

logger = structlog.get_logger(__name__)


class OperatorAuthService:
    """Manages operator authorization rules and logs access events."""

    def __init__(self) -> None:
        self._settings = get_settings()

    def get_authorized_ids(self) -> Set[int]:
        """Return the set of configured operator Telegram user IDs."""
        return set(self._settings.operator_ids)

    def is_authorized(self, user_id: Optional[int | str]) -> bool:
        """Check if a given user ID is in the configured operator whitelist.

        Spec v3.1 §3 & §14.3: Only authorized operator Telegram user IDs
        may use Accept or Reject. Taps from any other user ID are ignored and logged.
        """
        if user_id is None:
            return False
        try:
            uid_int = int(user_id)
        except (ValueError, TypeError):
            return False

        auth_ids = self.get_authorized_ids()
        # If no operators configured in development, log warning
        if not auth_ids and self._settings.app_env == "development":
            logger.warning("no_operators_configured_in_dev_allowing_all")
            return True

        allowed = uid_int in auth_ids
        if not allowed:
            logger.warning(
                "unauthorized_operator_access_attempt",
                user_id=uid_int,
                configured_operators=list(auth_ids),
            )
        return allowed
