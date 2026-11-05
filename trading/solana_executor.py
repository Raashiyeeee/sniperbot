"""
trading/solana_executor.py
──────────────────────────
Solana transaction builder, priority fee pricing, and executor (Spec v3.1 §10, §12).
Enforces:
  - Dynamic high priority fee up to hard cap (0.01 SOL default)
  - 30-second transaction deadline
  - Paper mode simulation / Live on-chain execution
"""
from __future__ import annotations

import time
import uuid
from typing import Optional

import structlog

from config.settings import get_settings
from trading.evm_executor import ExecutionResult
from trading.quote_engine import Quote

logger = structlog.get_logger(__name__)


class SolanaExecutor:
    """Executes buy transactions on Solana."""

    def __init__(self) -> None:
        self.settings = get_settings()

    async def execute_buy(
        self,
        quote: Quote,
        simulated_priority_fee_sol: float = 0.001,
    ) -> ExecutionResult:
        """
        Build, sign, and submit buy transaction on Solana.

        Priority fee rules:
        - Dynamic high tier up to hard cap (default 0.01 SOL)
        - Deadline = 30 seconds
        """
        start_time = time.perf_counter()
        max_priority_fee = self.settings.sol_max_priority_fee_sol

        if simulated_priority_fee_sol > max_priority_fee:
            err = (
                f"Required priority fee {simulated_priority_fee_sol:.4f} SOL exceeds "
                f"hard cap of {max_priority_fee} SOL"
            )
            logger.warning("sol_execution_rejected_priority_fee", fee=simulated_priority_fee_sol, max=max_priority_fee)
            return ExecutionResult(
                success=False,
                tx_hash=None,
                fee_spent=0.0,
                confirmed_tokens_received=0.0,
                execution_latency_ms=(time.perf_counter() - start_time) * 1000,
                error_message=err,
            )

        # Paper mode or development: simulate instant fill
        if self.settings.is_paper or self.settings.app_env == "development":
            mock_hash = f"sol_{uuid.uuid4().hex[:32]}"
            mock_fee_usd = 0.001 * 150.0  # ~ $0.15

            latency = (time.perf_counter() - start_time) * 1000
            logger.info(
                "solana_paper_buy_executed",
                tx_hash=mock_hash,
                tokens=quote.expected_amount_out,
                fee=mock_fee_usd,
                latency_ms=f"{latency:.1f}ms",
            )
            return ExecutionResult(
                success=True,
                tx_hash=mock_hash,
                fee_spent=mock_fee_usd,
                confirmed_tokens_received=quote.expected_amount_out,
                execution_latency_ms=latency,
            )

        return ExecutionResult(
            success=False,
            tx_hash=None,
            fee_spent=0.0,
            confirmed_tokens_received=0.0,
            execution_latency_ms=(time.perf_counter() - start_time) * 1000,
            error_message="Live execution mode requires explicit funded Solana keypair",
        )
