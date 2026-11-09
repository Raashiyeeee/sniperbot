"""
tests/test_shortlist_and_routes.py
──────────────────────────────────
Shortlist preload + launchpad smart route selection.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from database.models import Base, Project, ProjectStatus
from monitoring import approved_sources as as_mod
from projects.shortlist_loader import ShortlistLoader, load_config_file
from projects.sniper_config import LaunchMechanism, LaunchpadId, ProjectSniperConfig
from trading.launchpads import select_buy_route
from trading.launchpads.base import RouteKind
from trading.launchpads.registry import get_adapter, list_adapters


def test_sniper_config_normalizes_handles():
    cfg = ProjectSniperConfig(
        name="Demo",
        ticker="demo",
        official_x_handle="@FooBar",
        official_telegram="@MyChan",
        authorized_telegram_admins=[111, "222"],
        launchpad=LaunchpadId.BOXA,
        launch_mechanism=LaunchMechanism.BONDING_CURVE,
    )
    assert cfg.ticker == "DEMO"
    assert cfg.official_x_handle == "foobar"
    assert cfg.official_telegram == "mychan"
    assert cfg.authorized_telegram_admins == ["111", "222"]
    assert cfg.key == "demo"


def test_all_launchpad_adapters_registered():
    ids = set(list_adapters())
    for expected in (
        LaunchpadId.BOXA,
        LaunchpadId.KLEOS,
        LaunchpadId.ELEOS,
        LaunchpadId.ELYSPUMP,
        LaunchpadId.ASCEND,
        LaunchpadId.UNISWAP_V4,
        LaunchpadId.GENERIC_EVM,
    ):
        assert expected in ids
        assert get_adapter(expected).launchpad_id == expected


def test_route_selector_boxa_needs_router():
    async def _test():
        proj = Project(
            id="p-boxa",
            name="BoxaProj",
            ticker="BXA",
            chain_id="elysium",
            launchpad="boxa",
            launch_mechanism="bonding_curve",
            status=ProjectStatus.CHECKS_PASSED,
            ca_address="0xabc",
        )
        # No router -> not live
        plan = await select_buy_route(proj, "0xabc", 50.0)
        assert plan.trading_live is False

        proj.router_contract = "0xRouter"
        plan2 = await select_buy_route(proj, "0xabc", 50.0)
        assert plan2.is_executable
        assert plan2.launchpad == LaunchpadId.BOXA
        assert plan2.route_kind == RouteKind.BONDING_CURVE
        assert plan2.router_contract == "0xRouter"

    asyncio.run(_test())


def test_route_selector_uniswap_v4_needs_pool_manager():
    async def _test():
        proj = Project(
            id="p-v4",
            name="V4Proj",
            ticker="V4T",
            chain_id="elysium",
            launchpad="uniswap_v4",
            launch_mechanism="uniswap_v4",
            status=ProjectStatus.CHECKS_PASSED,
            ca_address="0xv4token",
            pool_manager="0xPoolManager",
            hook_address="0xHook",
            quote_asset="HYPE",
        )
        plan = await select_buy_route(proj, "0xv4token", 25.0)
        assert plan.is_executable
        assert plan.launchpad == LaunchpadId.UNISWAP_V4
        assert plan.route_kind == RouteKind.UNISWAP_V4_POOL
        assert plan.pool_manager == "0xPoolManager"

    asyncio.run(_test())


def test_shortlist_loader_upserts_and_registers(tmp_path: Path):
    async def _test():
        cfg = {
            "name": "Shortlisted Alpha",
            "ticker": "ALPHA",
            "chain_id": "elysium",
            "official_x_handle": "@AlphaOfficial",
            "official_telegram": "alphachan",
            "official_telegram_chat_id": "-100999",
            "authorized_telegram_admins": ["42"],
            "launchpad": "kleos",
            "launch_mechanism": "bonding_curve",
            "router_contract": "0xKleosRouter",
            "quote_asset": "HYPE",
            "auto_approve": True,
            "enabled": True,
        }
        path = tmp_path / "alpha.json"
        path.write_text(json.dumps(cfg), encoding="utf-8")

        engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        as_mod.approved_sources.replace_all({}, {})

        async with session_maker() as session:
            projects = await ShortlistLoader(session).preload(tmp_path)
            await session.commit()
            assert len(projects) == 1
            proj = projects[0]
            assert proj.ticker == "ALPHA"
            assert proj.status == ProjectStatus.APPROVED
            assert proj.is_shortlisted is True
            assert proj.launchpad == "kleos"
            assert proj.router_contract == "0xKleosRouter"
            assert proj.official_x_handle == "alphaofficial"
            assert proj.official_telegram_chat_id == "-100999"
            assert isinstance(proj.sniper_config, dict)
            assert "alphaofficial" in as_mod.approved_sources.allowed_x_handles()
            assert as_mod.approved_sources.is_allowed_telegram_chat("-100999")

            # Idempotent upsert
            again = await ShortlistLoader(session).preload(tmp_path)
            await session.commit()
            assert len(again) == 1
            assert again[0].id == proj.id

        await engine.dispose()

    asyncio.run(_test())


def test_template_file_validates():
    template = Path(__file__).resolve().parents[1] / "config" / "shortlist" / "_template.json"
    assert template.exists()
    # Loader skips _-prefixed files, but schema must still parse
    cfg = load_config_file(template)
    assert cfg.ticker == "EXMP"
    assert cfg.enabled is False
