"""
trading/quote_engine.py
───────────────────────
Quote generation, slippage limits, and price impact validation (Spec v3.1 §12.2, §12.2a).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import structlog

from config.settings import get_settings

logger = structlog.get_logger(__name__)


@dataclass
class Quote:
    chain_id: str
    token_in: str
    token_out: str
    amount_in_usd: float
    expected_amount_out: float
    min_amount_out: float
    estimated_price_impact_pct: float
    slippage_pct: float
    route: str
    is_valid: bool
    rejection_reason: Optional[str] = None


class QuoteEngine:
    """Calculates executable quotes and enforces slippage/impact limits."""

    def __init__(self) -> None:
        self.settings = get_settings()

    def get_quote(
        self,
        chain_id: str,
        ca_address: str,
        amount_usd: float,
        simulated_price: float = 1.0,
        simulated_impact_pct: float = 1.5,
    ) -> Quote:
        """
        Derive an execution quote and check slippage & price impact bounds.

        Robinhood Chain (EVM):
        - Max slippage: 5.0%
        - Max price impact: 5.0%

        Solana:
        - Max slippage: 5.0%
        """
        if chain_id == "robinhood":
            max_slippage = self.settings.evm_max_slippage_pct
            max_impact = self.settings.evm_max_price_impact_pct
            native_token = "ETH"
        elif chain_id == "solana":
            max_slippage = self.settings.sol_max_slippage_pct
            max_impact = 10.0  # fallback ceiling
            native_token = "SOL"
        else:
            max_slippage = 5.0
            max_impact = 5.0
            native_token = "NATIVE"

        # Check price impact
        if simulated_impact_pct > max_impact:
            msg = (
                f"Price impact {simulated_impact_pct:.2f}% exceeds max threshold "
                f"of {max_impact:.2f}% on {chain_id}"
            )
            logger.warning("quote_rejected_excessive_price_impact", impact=simulated_impact_pct, max=max_impact)
            return Quote(
                chain_id=chain_id,
                token_in=native_token,
                token_out=ca_address,
                amount_in_usd=amount_usd,
                expected_amount_out=0.0,
                min_amount_out=0.0,
                estimated_price_impact_pct=simulated_impact_pct,
                slippage_pct=max_slippage,
                route="primary_pool",
                is_valid=False,
                rejection_reason=msg,
            )

        expected_tokens = (amount_usd / simulated_price) if simulated_price > 0 else 0.0
        min_tokens = expected_tokens * (1.0 - (max_slippage / 100.0))

        return Quote(
            chain_id=chain_id,
            token_in=native_token,
            token_out=ca_address,
            amount_in_usd=amount_usd,
            expected_amount_out=expected_tokens,
            min_amount_out=min_tokens,
            estimated_price_impact_pct=simulated_impact_pct,
            slippage_pct=max_slippage,
            route="primary_pool",
            is_valid=True,
            rejection_reason=None,
        )
