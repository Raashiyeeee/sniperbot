"""
trading/order_manager.py
────────────────────────
Execution engine that runs the 2-second fast-path sequence (Spec v3.1 §10, §10.1).
Orchestrates:
  1. Circuit breaker / emergency check
  2. Wallet risk check (caps & positions)
  3. Status validation (must be CHECKS_PASSED)
  4. Quote generation and slippage/impact validation
  5. Transaction execution (Robinhood EVM or Solana)
  6. Order & Position state recording
  7. Audit trail generation
"""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Optional, Tuple

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import get_settings
from database.models import (
    AuditEvent,
    Order,
    OrderStatus,
    Position,
    PositionStatus,
    Project,
    ProjectStatus,
)
from projects.status_lifecycle import assert_valid_transition
from trading.emergency import EmergencyManager
from trading.evm_executor import EVMExecutor
from trading.quote_engine import QuoteEngine
from trading.solana_executor import SolanaExecutor
from trading.wallet_manager import WalletManager

logger = structlog.get_logger(__name__)


class OrderManager:
    """Manages order submission, execution, and position opening."""

    def __init__(
        self,
        session: AsyncSession,
        wallet_mgr: Optional[WalletManager] = None,
        quote_engine: Optional[QuoteEngine] = None,
        evm_exec: Optional[EVMExecutor] = None,
        sol_exec: Optional[SolanaExecutor] = None,
        emergency_mgr: Optional[EmergencyManager] = None,
    ) -> None:
        self._session = session
        self.wallet_mgr = wallet_mgr or WalletManager()
        self.quote_engine = quote_engine or QuoteEngine()
        self.evm_exec = evm_exec or EVMExecutor()
        self.sol_exec = sol_exec or SolanaExecutor()
        self.emergency_mgr = emergency_mgr or EmergencyManager()
        self.settings = get_settings()

    async def execute_approved_buy(
        self,
        project: Project,
        amount_usd: Optional[float] = None,
    ) -> Tuple[Optional[Order], Optional[Position], str]:
        """
        Execute an automated buy for an approved project whose checks have passed.
        Fast-path target: < 2 seconds from trigger to submission.
        """
        start_ts = time.perf_counter()
        trade_amount = amount_usd or self.settings.per_trade_spend_cap_usd
        chain_id = project.chain_id or "robinhood"
        ca_address = project.ca_address or ""

        # Step 1: Emergency & Circuit breakers check
        can_trade, breaker_reason = self.emergency_mgr.can_trade(chain_id, project.id)
        if not can_trade:
            logger.warning("buy_blocked_by_circuit_breaker", reason=breaker_reason)
            return None, None, breaker_reason

        # Step 2: Lifecycle check (must be CHECKS_PASSED)
        if project.status != ProjectStatus.CHECKS_PASSED:
            msg = f"Cannot buy: project status is {project.status}, must be CHECKS_PASSED"
            logger.warning("buy_blocked_wrong_status", status=project.status)
            return None, None, msg

        # Step 3: Wallet risk limits check (per-trade cap, daily caps, position limits)
        risk_check = self.wallet_mgr.check_trade_allowed(trade_amount)
        if not risk_check.allowed:
            logger.warning("buy_blocked_by_wallet_risk", reason=risk_check.reason)
            return None, None, f"Risk check failed: {risk_check.reason}"

        # Step 4: Quote validation
        quote = self.quote_engine.get_quote(chain_id, ca_address, trade_amount)
        if not quote.is_valid:
            logger.warning("buy_blocked_invalid_quote", reason=quote.rejection_reason)
            return None, None, f"Quote rejected: {quote.rejection_reason}"

        # Step 5: Transition to BUYING
        assert_valid_transition(project.status, ProjectStatus.BUYING, project.id)
        project.status = ProjectStatus.BUYING

        # Persist pending Order record
        order = Order(
            id=str(uuid.uuid4()),
            project_id=project.id,
            ca_address=ca_address,
            chain_id=chain_id,
            amount_usd=trade_amount,
            amount_native=None,
            quote_data={
                "expected_tokens": quote.expected_amount_out,
                "slippage_pct": quote.slippage_pct,
                "price_impact_pct": quote.estimated_price_impact_pct,
            },
            route=quote.route,
            status=OrderStatus.SUBMITTED,
            is_paper=self.settings.is_paper or (self.settings.app_env == "development"),
            submitted_at=datetime.now(timezone.utc),
        )
        self._session.add(order)
        await self._session.flush()

        # Step 6: Dispatch to blockchain executor
        if chain_id == "solana":
            exec_res = await self.sol_exec.execute_buy(quote)
        else:
            exec_res = await self.evm_exec.execute_buy(quote)

        total_latency_ms = (time.perf_counter() - start_ts) * 1000

        # Step 7: Handle execution outcome
        if exec_res.success:
            order.status = OrderStatus.FILLED
            order.tx_hash = exec_res.tx_hash
            order.fee_spent = exec_res.fee_spent
            order.confirmed_at = datetime.now(timezone.utc)

            # Open Position
            position = Position(
                id=str(uuid.uuid4()),
                project_id=project.id,
                token_address=ca_address,
                chain_id=chain_id,
                quantity=exec_res.confirmed_tokens_received,
                cost_basis_usd=trade_amount,
                current_value_usd=trade_amount,
                realized_pnl_usd=0.0,
                unrealized_pnl_usd=0.0,
                status=PositionStatus.OPEN,
                is_paper=order.is_paper,
                opened_at=datetime.now(timezone.utc),
            )
            self._session.add(position)

            # Move project to PURCHASED
            assert_valid_transition(project.status, ProjectStatus.PURCHASED, project.id)
            project.status = ProjectStatus.PURCHASED

            # Update wallet tracker
            self.wallet_mgr.record_trade_executed(trade_amount)

            # Audit event
            audit = AuditEvent(
                actor="system:execution_engine",
                action="buy_order_filled",
                entity_type="project",
                entity_id=project.id,
                old_state=ProjectStatus.BUYING,
                new_state=ProjectStatus.PURCHASED,
                reason=f"Buy filled in {total_latency_ms:.1f}ms (Target: 2000ms). Tx: {exec_res.tx_hash}",
            )
            self._session.add(audit)

            logger.info(
                "buy_order_completed",
                project_id=project.id,
                tx_hash=exec_res.tx_hash,
                tokens=exec_res.confirmed_tokens_received,
                latency_ms=f"{total_latency_ms:.1f}ms",
            )
            return order, position, f"Buy successfully executed in {total_latency_ms:.1f}ms"

        else:
            order.status = OrderStatus.FAILED
            order.error_message = exec_res.error_message

            # Move project to SAFETY_BLOCKED
            assert_valid_transition(project.status, ProjectStatus.SAFETY_BLOCKED, project.id)
            project.status = ProjectStatus.SAFETY_BLOCKED

            # Audit event
            audit = AuditEvent(
                actor="system:execution_engine",
                action="buy_order_failed",
                entity_type="project",
                entity_id=project.id,
                old_state=ProjectStatus.BUYING,
                new_state=ProjectStatus.SAFETY_BLOCKED,
                reason=f"Execution failed: {exec_res.error_message}",
            )
            self._session.add(audit)

            logger.error(
                "buy_order_failed",
                project_id=project.id,
                error=exec_res.error_message,
            )
            return order, None, f"Execution failed: {exec_res.error_message}"
