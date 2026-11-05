"""
volume/pool_volume_tracker.py
─────────────────────────────
Point-in-time snapshot volume checker for primary liquidity pool (Spec v3.1 §9, §9.1).
Takes a snapshot at the exact moment of CA detection, filters wash trades,
and evaluates genuine volume against the configured threshold (default $100).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional

import structlog

from config.settings import get_settings
from database.models import CheckResult
from volume.wash_trade_filters import TradeEvent, WashTradeFilter

logger = structlog.get_logger(__name__)


@dataclass
class VolumeSnapshotResult:
    status: CheckResult
    raw_volume_usd: float
    genuine_volume_usd: float
    pool_address: str
    threshold_usd: float
    snapshot_timestamp: datetime
    message: str


class PoolVolumeTracker:
    """Evaluates on-chain trading volume at CA detection snapshot."""

    def __init__(self, filter_instance: Optional[WashTradeFilter] = None) -> None:
        self.settings = get_settings()
        self.wash_filter = filter_instance or WashTradeFilter(
            round_trip_window_seconds=self.settings.wash_trade_round_trip_seconds
        )

    def evaluate_volume_snapshot(
        self,
        pool_address: str,
        recent_trades: List[TradeEvent],
        snapshot_time: Optional[datetime] = None,
    ) -> VolumeSnapshotResult:
        """
        Evaluate existing on-chain genuine volume.
        Spec v3.1 §9.1:
        - Instant snapshot at CA detection (no waiting window).
        - Filter out round-trip wash trades.
        - Sum buys + sells in primary pool.
        - >= $100 -> PASS. < $100 -> FAIL (move to Safety Blocked).
        """
        now = snapshot_time or datetime.now(timezone.utc)
        threshold = self.settings.min_qualifying_volume_usd

        raw_vol = sum(t.amount_usd for t in recent_trades)
        genuine_trades = self.wash_filter.filter_trades(recent_trades)
        genuine_vol = sum(t.amount_usd for t in genuine_trades)

        log = logger.bind(
            pool=pool_address,
            raw_vol=raw_vol,
            genuine_vol=genuine_vol,
            threshold=threshold,
        )

        if genuine_vol >= threshold:
            log.info("volume_check_passed")
            return VolumeSnapshotResult(
                status=CheckResult.PASS,
                raw_volume_usd=raw_vol,
                genuine_volume_usd=genuine_vol,
                pool_address=pool_address,
                threshold_usd=threshold,
                snapshot_timestamp=now,
                message=f"Genuine volume ${genuine_vol:.2f} meets requirement (>= ${threshold:.2f})",
            )
        else:
            log.warning("volume_check_failed_below_threshold")
            return VolumeSnapshotResult(
                status=CheckResult.FAIL,
                raw_volume_usd=raw_vol,
                genuine_volume_usd=genuine_vol,
                pool_address=pool_address,
                threshold_usd=threshold,
                snapshot_timestamp=now,
                message=(
                    f"Genuine volume ${genuine_vol:.2f} is below requirement (${threshold:.2f}). "
                    f"Action: move to SAFETY_BLOCKED."
                ),
            )
