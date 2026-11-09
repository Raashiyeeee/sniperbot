"""
projects/account_manager.py
───────────────────────────
Operator-driven attachment of official X / Telegram sources for approved projects.
Creates/updates SourceAccount rows, refreshes Telegram admin allowlists, and
registers targets in the approved_sources registry for continuous monitoring.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any, List, Optional, Tuple

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import (
    AuditEvent,
    Platform,
    Project,
    ProjectStatus,
    SourceAccount,
    SourceType,
)
from monitoring.approved_sources import approved_sources

logger = structlog.get_logger(__name__)

_CHAT_ID_RE = re.compile(r"^-?\d+$")
_TG_USERNAME_RE = re.compile(r"^@?[A-Za-z0-9_]{4,64}$")


def normalize_x_handle(raw: str) -> str:
    """
    Normalize an X handle or full profile URL to a clean lowercase username.
    Accepts:
      - https://x.com/username
      - http://twitter.com/username?s=20
      - @username
      - username
    """
    val = raw.strip()
    for prefix in (
        "https://x.com/", "http://x.com/",
        "https://www.x.com/", "http://www.x.com/",
        "https://twitter.com/", "http://twitter.com/",
        "https://www.twitter.com/", "http://www.twitter.com/",
        "x.com/", "twitter.com/", "www.x.com/", "www.twitter.com/",
    ):
        if val.lower().startswith(prefix):
            val = val[len(prefix):]
            break
    val = val.split("?")[0].split("/")[0].strip()
    return val.lstrip("@").strip().lower()


def parse_telegram_ref(raw: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Parse a Telegram reference into (chat_id, username_or_link).

    Accepts:
      - numeric chat id: -1001234567890
      - @channelusername
      - t.me/channelusername
    """
    value = raw.strip()
    if not value:
        return None, None

    lower = value.lower()
    if "t.me/" in lower:
        value = value.split("t.me/", 1)[1].split("?", 1)[0].strip("/")
        if value.startswith("+"):
            return None, value  # invite hash — store as handle only
        value = value.lstrip("@")

    if _CHAT_ID_RE.match(value):
        return value, None

    if _TG_USERNAME_RE.match(value):
        return None, value.lstrip("@").lower()

    return None, value.lstrip("@").lower()


def _admin_ids_payload(admin_ids: List[str]) -> dict:
    return {"admins": [str(a) for a in admin_ids]}


class AccountManager:
    """Attach and refresh official monitoring accounts for a project."""

    def __init__(self, session: AsyncSession, bot: Any = None) -> None:
        self._session = session
        self._bot = bot

    async def resolve_project(self, ref: str) -> Optional[Project]:
        """Resolve project by UUID, ticker, or official X handle."""
        ref = ref.strip()
        try:
            uuid.UUID(ref)
            proj = await self._session.get(Project, ref)
            if proj:
                return proj
        except ValueError:
            pass

        clean_handle = normalize_x_handle(ref)
        stmt = (
            select(Project)
            .where(
                (Project.ticker == ref.upper())
                | (Project.official_x_handle == clean_handle)
                | (Project.name == ref)
            )
            .order_by(Project.created_at.desc())
            .limit(1)
        )
        return await self._session.scalar(stmt)

    async def set_x_account(
        self,
        project: Project,
        handle: str,
        operator_id: str,
    ) -> SourceAccount:
        clean = normalize_x_handle(handle)
        if not clean:
            raise ValueError("X handle is required")

        project.official_x_handle = clean
        project.updated_at = datetime.now(timezone.utc)

        src = await self._upsert_source_account(
            platform=Platform.X,
            account_id=clean,
            handle=clean,
            authorized_admin_ids=None,
            operator_id=operator_id,
        )
        approved_sources.set_x_handle(clean, project.id)

        self._session.add(
            AuditEvent(
                actor=f"operator:{operator_id}",
                action="official_x_attached",
                entity_type="project",
                entity_id=project.id,
                reason=f"Official X set to @{clean}",
            )
        )
        logger.info("account_manager.x_set", project_id=project.id, handle=clean)
        return src

    async def set_telegram_account(
        self,
        project: Project,
        telegram_ref: str,
        operator_id: str,
    ) -> SourceAccount:
        chat_id, username = parse_telegram_ref(telegram_ref)
        if not chat_id and not username:
            raise ValueError("Telegram chat id or @username is required")

        # Prefer resolving @username -> chat_id via Bot API when available
        if not chat_id and username and self._bot is not None:
            try:
                chat = await self._bot.get_chat(f"@{username}")
                chat_id = str(chat.id)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "account_manager.telegram_resolve_failed",
                    username=username,
                    error=str(exc),
                )

        if username:
            project.official_telegram = username
        if chat_id:
            project.official_telegram_chat_id = str(chat_id)
        project.updated_at = datetime.now(timezone.utc)

        account_id = chat_id or (username or "")
        admin_ids: List[str] = []
        if chat_id and self._bot is not None:
            admin_ids = await self._fetch_telegram_admins(chat_id)

        src = await self._upsert_source_account(
            platform=Platform.TELEGRAM,
            account_id=account_id,
            handle=username or account_id,
            authorized_admin_ids=_admin_ids_payload(admin_ids) if admin_ids else {"admins": []},
            operator_id=operator_id,
        )

        if chat_id:
            approved_sources.set_telegram_chat(chat_id, project.id)

        self._session.add(
            AuditEvent(
                actor=f"operator:{operator_id}",
                action="official_telegram_attached",
                entity_type="project",
                entity_id=project.id,
                reason=(
                    f"Official Telegram set to chat_id={chat_id or 'unresolved'} "
                    f"username={username or 'n/a'} admins={len(admin_ids)}"
                ),
            )
        )
        logger.info(
            "account_manager.telegram_set",
            project_id=project.id,
            chat_id=chat_id,
            username=username,
            admin_count=len(admin_ids),
        )
        return src

    async def set_accounts(
        self,
        project: Project,
        operator_id: str,
        x_handle: Optional[str] = None,
        telegram_ref: Optional[str] = None,
    ) -> dict:
        result: dict = {"x": None, "telegram": None}
        if x_handle:
            result["x"] = await self.set_x_account(project, x_handle, operator_id)
        if telegram_ref:
            result["telegram"] = await self.set_telegram_account(project, telegram_ref, operator_id)
        if not x_handle and not telegram_ref:
            raise ValueError("Provide at least one of x_handle or telegram_ref")
        return result

    async def refresh_telegram_admins(self, project: Project, operator_id: str = "system") -> List[str]:
        """Re-fetch Telegram administrators for the project's official chat."""
        chat_id = project.official_telegram_chat_id
        if not chat_id:
            raise ValueError("Project has no official_telegram_chat_id")
        if self._bot is None:
            raise ValueError("Bot instance required to refresh Telegram admins")

        admin_ids = await self._fetch_telegram_admins(chat_id)
        src = await self.get_telegram_source_account(project)
        if src is None:
            src = await self._upsert_source_account(
                platform=Platform.TELEGRAM,
                account_id=str(chat_id),
                handle=project.official_telegram or str(chat_id),
                authorized_admin_ids=_admin_ids_payload(admin_ids),
                operator_id=operator_id,
            )
        else:
            src.authorized_admin_ids = _admin_ids_payload(admin_ids)
            src.updated_at = datetime.now(timezone.utc)

        approved_sources.set_telegram_chat(str(chat_id), project.id)
        logger.info(
            "account_manager.admins_refreshed",
            project_id=project.id,
            chat_id=chat_id,
            admin_count=len(admin_ids),
        )
        return admin_ids

    async def get_telegram_source_account(self, project: Project) -> Optional[SourceAccount]:
        account_id = project.official_telegram_chat_id or project.official_telegram
        if not account_id:
            return None
        stmt = select(SourceAccount).where(
            SourceAccount.platform == Platform.TELEGRAM,
            SourceAccount.account_id == str(account_id),
        )
        return await self._session.scalar(stmt)

    async def get_x_source_account(self, project: Project) -> Optional[SourceAccount]:
        if not project.official_x_handle:
            return None
        handle = project.official_x_handle.lstrip("@").lower()
        stmt = select(SourceAccount).where(
            SourceAccount.platform == Platform.X,
            SourceAccount.account_id == handle,
        )
        return await self._session.scalar(stmt)

    async def bootstrap_from_project(self, project: Project, operator_id: str) -> None:
        """On approve: register any already-known official accounts for monitoring."""
        if project.official_x_handle:
            await self.set_x_account(project, project.official_x_handle, operator_id)
        if project.official_telegram_chat_id or project.official_telegram:
            ref = project.official_telegram_chat_id or project.official_telegram or ""
            try:
                await self.set_telegram_account(project, ref, operator_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "account_manager.bootstrap_telegram_failed",
                    project_id=project.id,
                    error=str(exc),
                )

    async def reload_registry_from_db(self) -> None:
        """Hydrate approved_sources from APPROVED / CA_DETECTED projects."""
        stmt = select(Project).where(
            Project.status.in_([ProjectStatus.APPROVED, ProjectStatus.CA_DETECTED])
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        chats: dict = {}
        handles: dict = {}
        for p in rows:
            if p.official_telegram_chat_id:
                chats[str(p.official_telegram_chat_id)] = p.id
            if p.official_x_handle:
                handles[p.official_x_handle.lstrip("@").lower()] = p.id
        approved_sources.replace_all(chats, handles)

    async def _fetch_telegram_admins(self, chat_id: str) -> List[str]:
        assert self._bot is not None
        try:
            admins = await self._bot.get_chat_administrators(chat_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("account_manager.get_admins_failed", chat_id=chat_id, error=str(exc))
            return []
        ids: List[str] = []
        for member in admins:
            user = getattr(member, "user", None)
            if user is not None and getattr(user, "id", None) is not None:
                # Skip bots — only human admins authorize CA buys
                if getattr(user, "is_bot", False):
                    continue
                ids.append(str(user.id))
        return ids

    async def _upsert_source_account(
        self,
        platform: Platform,
        account_id: str,
        handle: Optional[str],
        authorized_admin_ids: Optional[dict],
        operator_id: str,
    ) -> SourceAccount:
        stmt = select(SourceAccount).where(
            SourceAccount.platform == platform,
            SourceAccount.account_id == account_id,
        )
        existing = await self._session.scalar(stmt)
        now = datetime.now(timezone.utc)
        if existing:
            existing.handle = handle or existing.handle
            existing.is_active = True
            existing.trust_verified = True
            existing.verified_by = str(operator_id)
            existing.verified_at = now
            existing.source_type = SourceType.OFFICIAL_PROJECT
            if authorized_admin_ids is not None:
                existing.authorized_admin_ids = authorized_admin_ids
            existing.updated_at = now
            return existing

        src = SourceAccount(
            id=str(uuid.uuid4()),
            platform=platform,
            account_id=account_id,
            handle=handle,
            source_type=SourceType.OFFICIAL_PROJECT,
            is_active=True,
            trust_verified=True,
            verified_by=str(operator_id),
            verified_at=now,
            authorized_admin_ids=authorized_admin_ids or {},
        )
        self._session.add(src)
        await self._session.flush()
        return src
