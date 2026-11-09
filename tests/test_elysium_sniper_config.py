"""
tests/test_elysium_sniper_config.py
───────────────────────────────────
Elysium Ultra-Fast Sniper configuration document defaults.
"""
from __future__ import annotations

import asyncio

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from config.chains import get_chain
from config.elysium_sniper import ELYSIUM_SNIPER, LaunchpadId
from config.settings import Settings
from database.models import Base, Order, OrderStatus, Position, PositionStatus, Project, ProjectStatus
from trading.order_manager import OrderManager


def test_network_section():
    assert ELYSIUM_SNIPER.network_name == "Elysium Mainnet"
    assert ELYSIUM_SNIPER.evm_chain_id == 99901
    assert ELYSIUM_SNIPER.gas_token == "HYPE"
    assert ELYSIUM_SNIPER.execution_target_ms == (1000, 5000)
    assert ELYSIUM_SNIPER.trading_mode == "automatic_buy"
    assert ELYSIUM_SNIPER.selling_mode == "manual_only"

    chain = get_chain("elysium")
    assert chain is not None
    assert chain.display_name == "Elysium Mainnet"
    assert chain.evm_chain_id == 99901
    assert chain.native_token == "HYPE"


def test_monitoring_and_buy_sections():
    m = ELYSIUM_SNIPER.monitoring
    assert m.x_monitoring == "primary"
    assert m.telegram_monitoring == "secondary"
    assert m.x_ca_detection is True
    assert m.telegram_ca_detection is True
    assert m.admin_verification_required is True
    assert m.ignore_normal_member_ca is True
    assert m.duplicate_ca_detection is True

    b = ELYSIUM_SNIPER.buy
    assert b.buy_amount_hype == 1.0
    assert b.max_slippage_pct == 3.0
    assert b.max_buy_tax_pct == 2.0
    assert b.min_liquidity_usd == 100.0
    assert b.gas_strategy == "dynamic_fast"
    assert b.transaction_simulation is True
    assert b.honeypot_check is True
    assert b.duplicate_purchases is False


def test_launchpads_and_sell_sections():
    assert LaunchpadId.BOXA in ELYSIUM_SNIPER.launchpads
    assert LaunchpadId.KLEOS in ELYSIUM_SNIPER.launchpads
    assert LaunchpadId.ELEOS in ELYSIUM_SNIPER.launchpads
    assert LaunchpadId.ELYSPUMP in ELYSIUM_SNIPER.launchpads
    assert LaunchpadId.ASCEND in ELYSIUM_SNIPER.launchpads
    assert LaunchpadId.UNISWAP_V4 in ELYSIUM_SNIPER.launchpads

    s = ELYSIUM_SNIPER.sell
    assert s.automatic_tp is False
    assert s.automatic_sl is False
    assert s.trailing_stop is False
    assert s.automatic_sell is False
    assert s.manual_sell is True
    assert s.emergency_sell_manual_only is True


def test_settings_defaults_align_with_document():
    # Construct without reading .env overrides for a clean check
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        app_env="development",
    )
    assert settings.elysium_buy_amount_hype == 0.05
    assert settings.max_buy_tax_pct == 2.0
    assert settings.evm_max_slippage_pct == 3.0
    assert settings.min_qualifying_volume_usd == 100.0
    assert settings.transaction_simulation_enabled is True
    assert settings.honeypot_check_enabled is True
    assert settings.allow_duplicate_purchases is False
    assert settings.automatic_sell is False
    assert settings.manual_sell_enabled is True
    assert settings.ca_execution_target_min_ms == 1000
    assert settings.ca_execution_target_max_ms == 5000
    assert settings.default_sniper_chain_id in ("elysium", "elysium_testnet")
    assert settings.evm_gas_strategy == "dynamic_fast"


def test_duplicate_purchase_blocked():
    async def _test():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with session_maker() as session:
            proj = Project(
                id="proj-dup-1",
                name="Dup",
                ticker="DUP",
                chain_id="elysium",
                ca_address="0xabc",
                launchpad="generic_evm",
                status=ProjectStatus.CHECKS_PASSED,
            )
            session.add(proj)
            session.add(
                Position(
                    id="pos-1",
                    project_id=proj.id,
                    token_address="0xabc",
                    chain_id="elysium",
                    quantity=1.0,
                    cost_basis_usd=1.0,
                    status=PositionStatus.OPEN,
                )
            )
            await session.commit()

            om = OrderManager(session)
            # Force the settings flag regardless of .env
            om.settings.allow_duplicate_purchases = False
            order, position, msg = await om.execute_approved_buy(proj, amount_usd=1.0)
            assert order is None
            assert position is None
            assert "Duplicate purchase disabled" in msg
        await engine.dispose()

    asyncio.run(_test())


def test_workflow_steps_cover_doc():
    steps = ELYSIUM_SNIPER.workflow_steps()
    assert any("Official X" in s for s in steps)
    assert any("Telegram Admins" in s for s in steps)
    assert any("Identify Launchpad" in s for s in steps)
    assert any("Simulate" in s for s in steps)
    assert any("Automatic Buy" in s for s in steps)
    assert any("Manual Selling" in s for s in steps)
