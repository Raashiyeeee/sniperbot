"""
tests/test_pre_buy_checks.py
────────────────────────────
Unit tests for Phase 4: Pre-buy checks & Volume Snapshot:
  - Buy tax and sell tax limit (3.0% max)
  - Tax reconciliation (taking higher of simulation & API)
  - Staleness (> 60s) -> UNKNOWN
  - Missing tax data -> UNKNOWN
  - Wash trade filter (5-minute round-trip removal)
  - Genuine volume snapshot threshold ($100 min)
  - Security scanner (honeypot, unsellable, liquidity route)
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from database.models import CheckResult
from security.contract_scanner import ContractSecurityScanner
from security.tax_checker import TaxChecker
from volume.pool_volume_tracker import PoolVolumeTracker
from volume.wash_trade_filters import TradeEvent, WashTradeFilter


class TestTaxChecker:
    def test_taxes_within_limits_pass(self):
        checker = TaxChecker()
        # Buy 2.5% <= 3.0%, Sell 2.0% <= 3.0%
        result = checker.evaluate_taxes(sim_buy_tax=2.5, sim_sell_tax=2.0)
        assert result.status == CheckResult.PASS
        assert result.buy_tax_pct == 2.5
        assert result.sell_tax_pct == 2.0

    def test_taxes_at_exact_limit_pass(self):
        checker = TaxChecker()
        # Exactly 3.0%
        result = checker.evaluate_taxes(sim_buy_tax=3.0, sim_sell_tax=3.0)
        assert result.status == CheckResult.PASS

    def test_buy_tax_above_limit_fails(self):
        checker = TaxChecker()
        # Buy 3.5% > 3.0%
        result = checker.evaluate_taxes(sim_buy_tax=3.5, sim_sell_tax=1.0)
        assert result.status == CheckResult.FAIL
        assert "Buy tax 3.5% exceeds max 3.0%" in result.message

    def test_sell_tax_above_limit_fails(self):
        checker = TaxChecker()
        # Sell 5.0% > 3.0%
        result = checker.evaluate_taxes(sim_buy_tax=1.0, sim_sell_tax=5.0)
        assert result.status == CheckResult.FAIL
        assert "Sell tax 5.0% exceeds max 3.0%" in result.message

    def test_reconciliation_uses_higher_value(self):
        checker = TaxChecker()
        # Simulation says 1.0% buy tax, but security API says 4.0%
        result = checker.evaluate_taxes(
            sim_buy_tax=1.0,
            sim_sell_tax=1.0,
            api_buy_tax=4.0,  # Higher!
            api_sell_tax=1.0,
        )
        assert result.status == CheckResult.FAIL  # 4.0% > 3.0%
        assert result.buy_tax_pct == 4.0

    def test_stale_simulation_returns_unknown(self):
        checker = TaxChecker()
        stale_time = datetime.now(timezone.utc) - timedelta(seconds=90)
        result = checker.evaluate_taxes(
            sim_buy_tax=1.0,
            sim_sell_tax=1.0,
            sim_timestamp=stale_time,
        )
        assert result.status == CheckResult.UNKNOWN
        assert "stale" in result.message

    def test_missing_simulation_returns_unknown(self):
        checker = TaxChecker()
        result = checker.evaluate_taxes(sim_buy_tax=None, sim_sell_tax=None)
        assert result.status == CheckResult.UNKNOWN


class TestWashTradeAndVolume:
    def test_wash_trade_round_trip_filtered(self):
        filt = WashTradeFilter(round_trip_window_seconds=300)
        now = datetime.now(timezone.utc)

        trades = [
            # Real buy from Alice
            TradeEvent("tx1", "0xAlice", is_buy=True, amount_usd=60.0, timestamp=now),
            # Wash trader Bob: buys $500 then sells $500 in 2 minutes
            TradeEvent("tx2", "0xBob", is_buy=True, amount_usd=500.0, timestamp=now + timedelta(seconds=10)),
            TradeEvent("tx3", "0xBob", is_buy=False, amount_usd=495.0, timestamp=now + timedelta(seconds=130)),
            # Real sell from Charlie
            TradeEvent("tx4", "0xCharlie", is_buy=False, amount_usd=50.0, timestamp=now + timedelta(seconds=150)),
        ]

        genuine = filt.filter_trades(trades)
        tx_hashes = [t.tx_hash for t in genuine]
        assert "tx1" in tx_hashes
        assert "tx4" in tx_hashes
        assert "tx2" not in tx_hashes  # Bob's wash buy removed
        assert "tx3" not in tx_hashes  # Bob's wash sell removed

    def test_volume_snapshot_passes_at_or_above_100(self):
        tracker = PoolVolumeTracker()
        now = datetime.now(timezone.utc)
        # $60 buy + $50 sell = $110 genuine volume
        trades = [
            TradeEvent("tx1", "0x1", is_buy=True, amount_usd=60.0, timestamp=now),
            TradeEvent("tx2", "0x2", is_buy=False, amount_usd=50.0, timestamp=now),
        ]
        res = tracker.evaluate_volume_snapshot("0xPool", trades, now)
        assert res.status == CheckResult.PASS
        assert res.genuine_volume_usd == 110.0

    def test_volume_snapshot_fails_below_100(self):
        tracker = PoolVolumeTracker()
        now = datetime.now(timezone.utc)
        # $40 genuine volume < $100
        trades = [
            TradeEvent("tx1", "0x1", is_buy=True, amount_usd=40.0, timestamp=now),
        ]
        res = tracker.evaluate_volume_snapshot("0xPool", trades, now)
        assert res.status == CheckResult.FAIL
        assert "SAFETY_BLOCKED" in res.message


class TestSecurityScanner:
    def test_clean_contract_passes(self):
        scanner = ContractSecurityScanner()
        res = scanner.evaluate_security("robinhood", "0xToken", is_sellable=True, is_honeypot=False)
        assert res.status == CheckResult.PASS

    def test_honeypot_contract_fails(self):
        scanner = ContractSecurityScanner()
        res = scanner.evaluate_security("robinhood", "0xHoneypot", is_sellable=False, is_honeypot=True)
        assert res.status == CheckResult.FAIL
        assert "HONEYPOT_DETECTED" in res.flags
