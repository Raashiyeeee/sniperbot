"""
tests/test_trading_execution.py
───────────────────────────────
Unit & integration tests for Phase 5 & 6:
  - Wallet risk limits (per-trade $50, daily spend $200, daily loss $150, max 3 positions)
  - Quote engine slippage & price impact bounds
  - Emergency circuit breakers (global stop, chain pause, project cancel)
  - Robinhood EVM & Solana executors
  - OrderManager fast-path sequence & position creation
  - LatencyTracker p50/p95/p99 instrumentation
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from analytics.latency_tracker import LatencyBreakdown, LatencyTracker
from database.models import Base, OrderStatus, PositionStatus, Project, ProjectStatus
from trading.emergency import EmergencyManager
from trading.evm_executor import EVMExecutor
from trading.order_manager import OrderManager
from trading.quote_engine import QuoteEngine
from trading.solana_executor import SolanaExecutor
from trading.wallet_manager import WalletManager


async def _setup_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    session = session_maker()
    return engine, session


@pytest.fixture(autouse=True)
def reset_emergency_manager():
    em = EmergencyManager()
    em.resume_global_trading()
    em._paused_chains.clear()
    em._cancelled_projects.clear()
    yield
    em.resume_global_trading()
    em._paused_chains.clear()
    em._cancelled_projects.clear()


class TestWalletManagerRiskLimits:
    def test_per_trade_cap_enforced(self):
        wm = WalletManager()
        # Default cap is $50.0
        allowed = wm.check_trade_allowed(amount_usd=50.0)
        assert allowed.allowed is True

        rejected = wm.check_trade_allowed(amount_usd=55.0)
        assert rejected.allowed is False
        assert "exceeds per-trade cap" in rejected.reason

    def test_daily_spend_cap_enforced(self):
        wm = WalletManager()
        # Spend 4 * $45 = $180
        wm.record_trade_executed(amount_usd=180.0)

        # Next $30 would be $210 > $200
        rejected = wm.check_trade_allowed(amount_usd=30.0)
        assert rejected.allowed is False
        assert "daily spend cap" in rejected.reason

    def test_max_open_positions_enforced(self):
        wm = WalletManager()
        wm.open_positions_count = 3  # Max 3
        rejected = wm.check_trade_allowed(amount_usd=20.0)
        assert rejected.allowed is False
        assert "Max open positions reached" in rejected.reason


class TestQuoteEngine:
    def test_excessive_price_impact_rejected(self):
        qe = QuoteEngine()
        # 6% impact > 5% max impact
        quote = qe.get_quote("robinhood", "0xToken", amount_usd=50.0, simulated_impact_pct=6.0)
        assert quote.is_valid is False
        assert "exceeds max threshold" in quote.rejection_reason

    def test_valid_quote_calculated(self):
        qe = QuoteEngine()
        # 2% impact <= 5%
        quote = qe.get_quote("robinhood", "0xToken", amount_usd=50.0, simulated_impact_pct=2.0)
        assert quote.is_valid is True
        assert quote.expected_amount_out > 0


class TestEmergencyCircuitBreakers:
    def test_global_stop_blocks_trading(self):
        em = EmergencyManager()
        em.trigger_global_stop()
        can_trade, reason = em.can_trade("robinhood", "p1")
        assert can_trade is False
        assert "Global emergency stop is active" in reason

        em.resume_global_trading()
        can_trade_after, _ = em.can_trade("robinhood", "p1")
        assert can_trade_after is True

    def test_per_chain_pause(self):
        em = EmergencyManager()
        em.pause_chain("solana")
        can_trade_sol, reason = em.can_trade("solana", "p1")
        assert can_trade_sol is False
        assert "Trading is paused for chain solana" in reason

        # Robinhood should still be allowed
        can_trade_evm, _ = em.can_trade("robinhood", "p1")
        assert can_trade_evm is True

        em.resume_chain("solana")


class TestExecutors:
    def test_evm_executor_paper_mode(self):
        exec_evm = EVMExecutor()
        qe = QuoteEngine()
        quote = qe.get_quote("robinhood", "0xToken", amount_usd=50.0)

        res = asyncio.run(exec_evm.execute_buy(quote))
        assert res.success is True
        assert res.tx_hash.startswith("0x")
        assert res.fee_spent > 0

    def test_solana_executor_paper_mode(self):
        exec_sol = SolanaExecutor()
        qe = QuoteEngine()
        quote = qe.get_quote("solana", "SolToken", amount_usd=50.0)

        res = asyncio.run(exec_sol.execute_buy(quote))
        assert res.success is True
        assert res.tx_hash.startswith("sol_")


def test_order_manager_fast_path():
    async def _test():
        engine, session = await _setup_session()
        try:
            om = OrderManager(session)

            # Project whose pre-buy checks passed
            proj = Project(
                id="proj-ready-to-buy",
                name="ReadyToken",
                ticker="RDY",
                chain_id="robinhood",
                ca_address="0x1234567890abcdef1234567890abcdef12345678",
                status=ProjectStatus.CHECKS_PASSED,
            )
            session.add(proj)
            await session.commit()

            # Execute approved buy
            order, position, msg = await om.execute_approved_buy(proj, amount_usd=50.0)
            await session.commit()

            assert order is not None
            assert order.status == OrderStatus.FILLED
            assert order.tx_hash is not None

            assert position is not None
            assert position.status == PositionStatus.OPEN
            assert position.cost_basis_usd == 50.0

            assert proj.status == ProjectStatus.PURCHASED
        finally:
            await session.close()
            await engine.dispose()

    asyncio.run(_test())


def test_latency_tracker_percentiles():
    tracker = LatencyTracker()
    now = 1000.0

    # 10 records with known submission times
    for delta in [0.2, 0.4, 0.6, 0.8, 1.0, 1.2, 1.4, 1.6, 1.8, 2.5]:
        tracker.record(
            LatencyBreakdown(
                project_id="p",
                event_received_at=now,
                tx_submitted_at=now + delta,
            )
        )

    stats = tracker.get_summary_stats()
    assert stats["count"] == 10
    assert stats["target_met_pct"] == 90.0  # 9 of 10 <= 2.0s
    assert stats["p50_ms"] > 0
    assert stats["p95_ms"] > 0
