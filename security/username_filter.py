"""
security/username_filter.py
───────────────────────────
Filters out X accounts with verified username-change history (Spec v1.3).
Excludes accounts with rename or recycling history from normal launch notifications.
Does not pretend an unavailable history has been verified.
"""
from __future__ import annotations

from typing import Optional, Tuple
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Platform, SourceAccount, SourceType

logger = structlog.get_logger(__name__)


class UsernameChangeFilter:
    """Detects and excludes X accounts with verified username-change history."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._log = logger.bind(component="UsernameChangeFilter")

    async def check_account(
        self,
        platform: str,
        account_id: Optional[str],
        current_handle: Optional[str],
        raw_payload: Optional[dict] = None,
    ) -> Tuple[bool, Optional[str]]:
        """
        Check if an account has a verified username-change history.

        Parameters
        ----------
        platform:
            "x" or "telegram"
        account_id:
            Immutable platform user ID (e.g. numeric Twitter user ID).
        current_handle:
            Current username/screen_name.
        raw_payload:
            Raw message or tweet dictionary for metadata inspection.

        Returns
        -------
        Tuple[is_excluded, reason]
            is_excluded: True if account must be excluded from launch alerts.
            reason: Explanation if excluded, otherwise None.
        """
        if platform.lower() != "x" or not current_handle:
            return False, None

        clean_handle = current_handle.lstrip("@").lower()
        raw = raw_payload or {}

        # 1. Check if raw API metadata provides verified previous usernames
        user_info = raw.get("user_info") or raw.get("user") or {}
        prev_usernames = (
            raw.get("previous_usernames")
            or user_info.get("previous_usernames")
            or user_info.get("screen_name_history")
            or []
        )
        if isinstance(prev_usernames, list) and len(prev_usernames) > 0:
            msg = f"Verified username changes in API history: {prev_usernames}"
            self._log.warning("account_excluded_api_history", handle=clean_handle, history=prev_usernames)
            return True, msg

        # 2. Check persistent database history using numeric account ID
        if not account_id or account_id == "unknown":
            account_id = str(user_info.get("id") or user_info.get("rest_id") or "")

        # If immutable numeric account ID is not available from X,
        # the bot must not pretend an unavailable history has been verified.
        if not account_id or account_id == "unknown":
            return False, None

        stmt = select(SourceAccount).where(
            SourceAccount.platform == Platform.X,
            SourceAccount.account_id == str(account_id),
        )
        existing = (await self._session.execute(stmt)).scalar_one_or_none()

        if existing:
            stored_handle = (existing.handle or "").lstrip("@").lower()
            if stored_handle and stored_handle != clean_handle:
                # Verified change: this numeric account was previously recorded with a different handle
                existing.has_username_changes = True
                changes = list(existing.username_changes or [])
                if stored_handle not in changes:
                    changes.append(stored_handle)
                existing.username_changes = changes
                existing.handle = clean_handle
                await self._session.commit()
                msg = f"Account renamed from @{stored_handle} to @{clean_handle}"
                self._log.warning("account_excluded_verified_rename", account_id=account_id, old=stored_handle, new=clean_handle)
                return True, msg

            if existing.has_username_changes:
                msg = f"Account has verified rename history: {existing.username_changes}"
                self._log.warning("account_excluded_prior_rename_history", account_id=account_id, changes=existing.username_changes)
                return True, msg
        else:
            # Register new account record for future change tracking
            account = SourceAccount(
                platform=Platform.X,
                account_id=str(account_id),
                handle=clean_handle,
                source_type=SourceType.OFFICIAL_PROJECT,
                has_username_changes=False,
                username_changes=[],
            )
            self._session.add(account)
            await self._session.commit()

        return False, None
