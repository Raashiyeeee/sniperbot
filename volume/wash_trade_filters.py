"""
volume/wash_trade_filters.py
────────────────────────────
Filters out wash-trading and self-trading volume (Spec v3.1 §7, §9, §9.1).
Detects rapid round-trips (buy -> sell or sell -> buy by the same wallet within 5 minutes).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Set

import structlog

from config.settings import get_settings

logger = structlog.get_logger(__name__)


@dataclass
class TradeEvent:
    tx_hash: str
    wallet_address: str
    is_buy: bool
    amount_usd: float
    timestamp: datetime


class WashTradeFilter:
    """Filters out artificial and circular volume from trading pools."""

    def __init__(self, round_trip_window_seconds: int = 300) -> None:
        self.window_seconds = round_trip_window_seconds
        self.settings = get_settings()

    def filter_trades(self, trades: List[TradeEvent]) -> List[TradeEvent]:
        """
        Evaluate a sequence of trades in a pool and remove wash trades.

        Rules:
        1. If a wallet buys and then sells (or sells then buys) within 5 minutes (300s),
           exclude those transactions from the genuine volume calculation.
        2. If a wallet does > 3 round trips, flag wallet as suspicious and exclude all their trades.
        """
        if not trades:
            return []

        # Group trades by wallet
        wallet_trades: Dict[str, List[TradeEvent]] = {}
        for t in trades:
            wallet_trades.setdefault(t.wallet_address.lower(), []).append(t)

        excluded_tx_hashes: Set[str] = set()

        for wallet, w_trades in wallet_trades.items():
            # Sort trades by timestamp
            sorted_trades = sorted(w_trades, key=lambda x: x.timestamp)
            round_trip_count = 0

            # Scan for round trips
            for i in range(len(sorted_trades)):
                t1 = sorted_trades[i]
                for j in range(i + 1, len(sorted_trades)):
                    t2 = sorted_trades[j]
                    delta = (t2.timestamp - t1.timestamp).total_seconds()
                    if delta > self.window_seconds:
                        break  # Outside 5-minute window

                    # Round-trip detected if opposite directions (buy & sell)
                    if t1.is_buy != t2.is_buy:
                        round_trip_count += 1
                        excluded_tx_hashes.add(t1.tx_hash)
                        excluded_tx_hashes.add(t2.tx_hash)

            # Rule 2: > 3 round trips -> exclude all trades from this wallet
            if round_trip_count >= 3:
                logger.warning(
                    "persistent_wash_trader_flagged",
                    wallet=wallet,
                    round_trips=round_trip_count,
                )
                for t in sorted_trades:
                    excluded_tx_hashes.add(t.tx_hash)

        # Return only genuine trades
        genuine_trades = [t for t in trades if t.tx_hash not in excluded_tx_hashes]

        filtered_count = len(trades) - len(genuine_trades)
        if filtered_count > 0:
            logger.info(
                "wash_trades_filtered",
                total_trades=len(trades),
                wash_trades_removed=filtered_count,
                genuine_trades_kept=len(genuine_trades),
            )

        return genuine_trades
