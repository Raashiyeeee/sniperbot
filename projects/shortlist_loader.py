"""
projects/shortlist_loader.py
────────────────────────────
Load preconfigured shortlisted project docs from config/shortlist/*.json
and upsert them as APPROVED monitoring targets (source of truth for sniping).
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import get_settings
from database.models import (
    AuditEvent,
    Platform,
    Project,
    ProjectStatus,
    SourceAccount,
    SourceType,
)
from monitoring.approved_sources import approved_sources
from projects.sniper_config import ProjectSniperConfig

logger = structlog.get_logger(__name__)

DEFAULT_SHORTLIST_DIR = Path(__file__).resolve().parent.parent / "config" / "shortlist"


def discover_shortlist_files(directory: Path) -> List[Path]:
    if not directory.is_dir():
        return []
    files = sorted(directory.glob("*.json"))
    return [p for p in files if not p.name.startswith("_")]


def load_config_file(path: Path) -> ProjectSniperConfig:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and "slug" not in data:
        data["slug"] = path.stem
    return ProjectSniperConfig.model_validate(data)


def load_all_configs(directory: Optional[Path] = None) -> List[ProjectSniperConfig]:
    root = directory or Path(get_settings().shortlist_dir or str(DEFAULT_SHORTLIST_DIR))
    configs: List[ProjectSniperConfig] = []
    for path in discover_shortlist_files(root):
        try:
            cfg = load_config_file(path)
            if cfg.enabled:
                configs.append(cfg)
            else:
                logger.info("shortlist.skipped_disabled", file=str(path), ticker=cfg.ticker)
        except Exception as exc:  # noqa: BLE001
            logger.error("shortlist.load_failed", file=str(path), error=str(exc))
    return configs


class ShortlistLoader:
    """Upsert shortlisted project docs into DB and monitoring registry."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def preload(self, directory: Optional[Path] = None) -> List[Project]:
        configs = load_all_configs(directory)
        projects: List[Project] = []
        for cfg in configs:
            proj = await self.upsert_config(cfg)
            projects.append(proj)
        await self._session.flush()
        logger.info("shortlist.preload_complete", count=len(projects))
        return projects

    async def upsert_config(self, cfg: ProjectSniperConfig) -> Project:
        project = await self._find_existing(cfg)
        now = datetime.now(timezone.utc)

        if project is None:
            project = Project(
                id=str(uuid.uuid4()),
                name=cfg.name,
                ticker=cfg.ticker,
                chain_id=cfg.chain_id,
                status=ProjectStatus.APPROVED if cfg.auto_approve else ProjectStatus.PENDING_APPROVAL,
                is_shortlisted=True,
                created_at=now,
                updated_at=now,
            )
            self._session.add(project)
            await self._session.flush()
            created = True
        else:
            created = False
            project.is_shortlisted = True
            if cfg.auto_approve and project.status in (
                ProjectStatus.DETECTED,
                ProjectStatus.PENDING_APPROVAL,
            ):
                project.status = ProjectStatus.APPROVED

        # Apply documentation as source of truth
        project.name = cfg.name
        project.ticker = cfg.ticker
        project.chain_id = cfg.chain_id
        project.official_x_handle = cfg.official_x_handle
        project.official_telegram = cfg.official_telegram
        if cfg.official_telegram_chat_id:
            project.official_telegram_chat_id = str(cfg.official_telegram_chat_id)
        project.website_url = cfg.website_url
        project.documentation_url = cfg.documentation_url
        project.launchpad = cfg.launchpad.value
        project.launch_mechanism = cfg.launch_mechanism.value
        project.router_contract = cfg.router_contract
        project.pool_manager = cfg.pool_manager
        project.hook_address = cfg.hook_address
        project.quote_asset = cfg.quote_asset
        project.expected_ca_source = cfg.expected_ca_source
        if cfg.token_address and not project.ca_address:
            project.ca_address = cfg.token_address
        project.sniper_config = cfg.model_dump(mode="json")
        project.updated_at = now

        await self._sync_source_accounts(project, cfg)
        self._register_monitoring(project)

        self._session.add(
            AuditEvent(
                actor="system:shortlist_loader",
                action="shortlist_upserted" if not created else "shortlist_created",
                entity_type="project",
                entity_id=project.id,
                reason=f"Preloaded shortlist config for {cfg.ticker} ({cfg.launchpad.value})",
            )
        )
        logger.info(
            "shortlist.project_ready",
            project_id=project.id,
            ticker=project.ticker,
            launchpad=project.launchpad,
            created=created,
        )
        return project

    async def _find_existing(self, cfg: ProjectSniperConfig) -> Optional[Project]:
        # Prefer ticker+chain
        stmt = (
            select(Project)
            .where(Project.ticker == cfg.ticker)
            .where(Project.chain_id == cfg.chain_id)
            .limit(1)
        )
        found = await self._session.scalar(stmt)
        if found:
            return found

        if cfg.official_x_handle:
            stmt = (
                select(Project)
                .where(Project.official_x_handle == cfg.official_x_handle)
                .limit(1)
            )
            found = await self._session.scalar(stmt)
            if found:
                return found

        if cfg.official_telegram_chat_id:
            stmt = (
                select(Project)
                .where(Project.official_telegram_chat_id == str(cfg.official_telegram_chat_id))
                .limit(1)
            )
            return await self._session.scalar(stmt)
        return None

    async def _sync_source_accounts(self, project: Project, cfg: ProjectSniperConfig) -> None:
        if cfg.official_x_handle:
            await self._upsert_source(
                platform=Platform.X,
                account_id=cfg.official_x_handle,
                handle=cfg.official_x_handle,
                authorized_admin_ids=None,
            )

        tg_account_id = cfg.official_telegram_chat_id or cfg.official_telegram
        if tg_account_id:
            admins = {"admins": list(cfg.authorized_telegram_admins)}
            await self._upsert_source(
                platform=Platform.TELEGRAM,
                account_id=str(tg_account_id),
                handle=cfg.official_telegram or str(tg_account_id),
                authorized_admin_ids=admins,
            )

    async def _upsert_source(
        self,
        platform: Platform,
        account_id: str,
        handle: Optional[str],
        authorized_admin_ids: Optional[dict],
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
            existing.source_type = SourceType.OFFICIAL_PROJECT
            existing.verified_by = "shortlist"
            existing.verified_at = now
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
            verified_by="shortlist",
            verified_at=now,
            authorized_admin_ids=authorized_admin_ids or {},
        )
        self._session.add(src)
        await self._session.flush()
        return src

    @staticmethod
    def _register_monitoring(project: Project) -> None:
        if project.official_x_handle:
            approved_sources.set_x_handle(project.official_x_handle, project.id)
        if project.official_telegram_chat_id:
            approved_sources.set_telegram_chat(str(project.official_telegram_chat_id), project.id)
