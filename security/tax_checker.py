"""
security/tax_checker.py
───────────────────────
Buy and sell tax measurement and validation (Spec v3.1 §9, §9.2).
Enforces configurable max tax limits (default 2.0%).
Uses on-chain simulation as primary source, security APIs as secondary sanity check.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import structlog

from config.settings import get_settings
from database.models import CheckResult

logger = structlog.get_logger(__name__)


@dataclass
class TaxCheckResult:
    status: CheckResult
    buy_tax_pct: Optional[float]
    sell_tax_pct: Optional[float]
    max_buy_tax_pct: float
    max_sell_tax_pct: float
    provider: str
    simulated_at: datetime
    message: str


class TaxChecker:
    """Measures and validates contract taxes before buy execution."""

    def __init__(self) -> None:
        self.settings = get_settings()

    def evaluate_taxes(
        self,
        sim_buy_tax: Optional[float],
        sim_sell_tax: Optional[float],
        api_buy_tax: Optional[float] = None,
        api_sell_tax: Optional[float] = None,
        sim_timestamp: Optional[datetime] = None,
    ) -> TaxCheckResult:
        """
        Evaluate effective taxes against configured limits.

        Rules from Spec v3.1 §9.2:
        1. If simulation result is missing or timed out -> UNKNOWN -> do not buy.
        2. If simulation result is > 60 seconds old -> Mark stale/UNKNOWN.
        3. If simulation and API disagree -> Use the HIGHER value.
        4. Buy tax must be <= max_buy_tax_pct (default 2.0%).
        5. Sell tax must be <= max_sell_tax_pct (default 2.0%).
        """
        now = datetime.now(timezone.utc)
        max_buy = self.settings.max_buy_tax_pct
        max_sell = self.settings.max_sell_tax_pct

        # Check 1: Missing simulation
        if sim_buy_tax is None or sim_sell_tax is None:
            logger.warning("tax_check_unknown_missing_simulation")
            return TaxCheckResult(
                status=CheckResult.UNKNOWN,
                buy_tax_pct=None,
                sell_tax_pct=None,
                max_buy_tax_pct=max_buy,
                max_sell_tax_pct=max_sell,
                provider="simulation",
                simulated_at=now,
                message="Tax check UNKNOWN: simulation data unavailable or timed out. Do not buy.",
            )

        # Check 2: Staleness check (> 60 seconds)
        if sim_timestamp:
            elapsed = (now - sim_timestamp).total_seconds()
            if elapsed > 60:
                logger.warning("tax_check_stale_simulation", elapsed_seconds=elapsed)
                return TaxCheckResult(
                    status=CheckResult.UNKNOWN,
                    buy_tax_pct=sim_buy_tax,
                    sell_tax_pct=sim_sell_tax,
                    max_buy_tax_pct=max_buy,
                    max_sell_tax_pct=max_sell,
                    provider="simulation",
                    simulated_at=sim_timestamp,
                    message=f"Tax simulation is stale ({elapsed:.1f}s > 60s). Re-run required.",
                )

        # Check 3: Reconcile with secondary security API (take higher)
        effective_buy = sim_buy_tax
        if api_buy_tax is not None:
            if api_buy_tax != sim_buy_tax:
                logger.info(
                    "tax_discrepancy_buy",
                    sim=sim_buy_tax,
                    api=api_buy_tax,
                    using=max(sim_buy_tax, api_buy_tax),
                )
            effective_buy = max(sim_buy_tax, api_buy_tax)

        effective_sell = sim_sell_tax
        if api_sell_tax is not None:
            if api_sell_tax != sim_sell_tax:
                logger.info(
                    "tax_discrepancy_sell",
                    sim=sim_sell_tax,
                    api=api_sell_tax,
                    using=max(sim_sell_tax, api_sell_tax),
                )
            effective_sell = max(sim_sell_tax, api_sell_tax)

        # Check 4 & 5: Validate against limits
        buy_passes = effective_buy <= max_buy
        sell_passes = effective_sell <= max_sell

        if buy_passes and sell_passes:
            logger.info("tax_check_passed", buy_tax=effective_buy, sell_tax=effective_sell)
            return TaxCheckResult(
                status=CheckResult.PASS,
                buy_tax_pct=effective_buy,
                sell_tax_pct=effective_sell,
                max_buy_tax_pct=max_buy,
                max_sell_tax_pct=max_sell,
                provider="simulation_reconciled",
                simulated_at=now,
                message=f"Taxes pass: Buy {effective_buy:.1f}% <= {max_buy}%, Sell {effective_sell:.1f}% <= {max_sell}%",
            )
        else:
            reasons = []
            if not buy_passes:
                reasons.append(f"Buy tax {effective_buy:.1f}% exceeds max {max_buy}%")
            if not sell_passes:
                reasons.append(f"Sell tax {effective_sell:.1f}% exceeds max {max_sell}%")

            fail_msg = " | ".join(reasons)
            logger.warning("tax_check_failed", reason=fail_msg)
            return TaxCheckResult(
                status=CheckResult.FAIL,
                buy_tax_pct=effective_buy,
                sell_tax_pct=effective_sell,
                max_buy_tax_pct=max_buy,
                max_sell_tax_pct=max_sell,
                provider="simulation_reconciled",
                simulated_at=now,
                message=f"Trade rejected: {fail_msg}",
            )
