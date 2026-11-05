"""
trading/wallet_manager.py
─────────────────────────
Dedicated wallet management and capital risk controls (Spec v3.1 §12, §12.2, §12.2a).
Enforces:
  - Per-trade spend cap ($50 default)
  - Daily spend cap ($200 default)
  - Daily loss cap ($150 default)
  - Maximum concurrent open positions (3 default)
Never exposes private keys in logs or exceptions.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Tuple

import structlog

from config.settings import get_settings

logger = structlog.get_logger(__name__)


@dataclass
class RiskCheckResult:
    allowed: bool
    reason: str


class WalletManager:
    """Manages trade capital allocation and hard risk limits."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self.daily_spent_usd: float = 0.0
        self.daily_loss_usd: float = 0.0
        self.open_positions_count: int = 0
        self.last_reset_date = datetime.now(timezone.utc).date()

    def _check_and_reset_daily(self) -> None:
        """Reset daily counters at midnight UTC."""
        today = datetime.now(timezone.utc).date()
        if today > self.last_reset_date:
            logger.info(
                "daily_risk_limits_reset",
                previous_date=str(self.last_reset_date),
                today=str(today),
            )
            self.daily_spent_usd = 0.0
            self.daily_loss_usd = 0.0
            self.last_reset_date = today

    def check_trade_allowed(
        self,
        amount_usd: float,
        current_open_positions: Optional[int] = None,
    ) -> RiskCheckResult:
        """
        Verify that a proposed trade does not exceed any configured risk caps.

        Spec v3.1 §12.2a:
        - Per-trade cap: $50
        - Daily spend cap: $200
        - Daily loss cap: $150
        - Max open positions: 3
        """
        self._check_and_reset_daily()

        per_trade_cap = self.settings.per_trade_spend_cap_usd
        daily_spend_cap = self.settings.daily_spend_cap_usd
        daily_loss_cap = self.settings.daily_loss_cap_usd
        max_positions = self.settings.max_open_positions

        active_positions = (
            current_open_positions if current_open_positions is not None else self.open_positions_count
        )

        # 1. Per-trade cap
        if amount_usd > per_trade_cap:
            msg = f"Trade amount ${amount_usd:.2f} exceeds per-trade cap of ${per_trade_cap:.2f}"
            logger.warning("risk_limit_breached_per_trade", amount=amount_usd, cap=per_trade_cap)
            return RiskCheckResult(allowed=False, reason=msg)

        # 2. Daily spend cap
        if (self.daily_spent_usd + amount_usd) > daily_spend_cap:
            msg = (
                f"Proposed trade would push daily spend (${self.daily_spent_usd + amount_usd:.2f}) "
                f"over daily spend cap of ${daily_spend_cap:.2f}"
            )
            logger.warning("risk_limit_breached_daily_spend", total=self.daily_spent_usd + amount_usd, cap=daily_spend_cap)
            return RiskCheckResult(allowed=False, reason=msg)

        # 3. Daily loss cap
        if self.daily_loss_usd >= daily_loss_cap:
            msg = f"Daily loss cap reached (${self.daily_loss_usd:.2f} >= ${daily_loss_cap:.2f}). Trading halted."
            logger.warning("risk_limit_breached_daily_loss", loss=self.daily_loss_usd, cap=daily_loss_cap)
            return RiskCheckResult(allowed=False, reason=msg)

        # 4. Max concurrent open positions
        if active_positions >= max_positions:
            msg = f"Max open positions reached ({active_positions} >= {max_positions})"
            logger.warning("risk_limit_breached_max_positions", count=active_positions, max=max_positions)
            return RiskCheckResult(allowed=False, reason=msg)

        return RiskCheckResult(allowed=True, reason="All risk checks passed")

    def record_trade_executed(self, amount_usd: float) -> None:
        """Update tracker after a buy fills."""
        self._check_and_reset_daily()
        self.daily_spent_usd += amount_usd
        self.open_positions_count += 1
        logger.info(
            "wallet_state_updated",
            daily_spent=self.daily_spent_usd,
            open_positions=self.open_positions_count,
        )

    def record_position_closed(self, realized_pnl_usd: float) -> None:
        """Update tracker after a position closes."""
        self._check_and_reset_daily()
        self.open_positions_count = max(0, self.open_positions_count - 1)
        if realized_pnl_usd < 0:
            self.daily_loss_usd += abs(realized_pnl_usd)
        logger.info(
            "position_closed_recorded",
            pnl=realized_pnl_usd,
            daily_loss=self.daily_loss_usd,
            open_positions=self.open_positions_count,
        )
