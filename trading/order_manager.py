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

from config.chains import is_evm_chain
from config.settings import get_settings
from sqlalchemy import select

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
from trading.launchpads import select_buy_route
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

    async def _duplicate_purchase_block_reason(
        self,
        project: Project,
        ca_address: str,
    ) -> Optional[str]:
        """Return a block reason when duplicate purchases are disabled."""
        if project.status == ProjectStatus.PURCHASED:
            return "Duplicate purchase disabled: project already purchased"

        open_pos = await self._session.scalar(
            select(Position).where(
                Position.project_id == project.id,
                Position.status == PositionStatus.OPEN,
            )
        )
        if open_pos is not None:
            return "Duplicate purchase disabled: open position already exists"

        if ca_address:
            filled = await self._session.scalar(
                select(Order).where(
                    Order.project_id == project.id,
                    Order.ca_address == ca_address,
                    Order.status.in_([OrderStatus.FILLED, OrderStatus.CONFIRMED]),
                )
            )
            if filled is not None:
                return "Duplicate purchase disabled: filled order already exists for this CA"
        return None

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
        chain_id = (project.chain_id or "robinhood").lower()
        ca_address = project.ca_address or ""

        # Elysium Ultra-Fast Sniper: default buy = 1 HYPE (mapped to USD for risk caps)
        if amount_usd is not None:
            trade_amount = amount_usd
        elif chain_id in ("elysium", "elysium_testnet"):
            trade_amount = self.settings.elysium_buy_amount_usd
        else:
            trade_amount = self.settings.per_trade_spend_cap_usd

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

        # Step 2b: Duplicate purchases disabled (Ultra-Fast Sniper §3)
        if not self.settings.allow_duplicate_purchases:
            dup_msg = await self._duplicate_purchase_block_reason(project, ca_address)
            if dup_msg:
                logger.warning("buy_blocked_duplicate_purchase", reason=dup_msg)
                return None, None, dup_msg

        # Step 3: Wallet risk limits check (per-trade cap, daily caps, position limits)
        risk_check = self.wallet_mgr.check_trade_allowed(trade_amount)
        if not risk_check.allowed:
            logger.warning("buy_blocked_by_wallet_risk", reason=risk_check.reason)
            return None, None, f"Risk check failed: {risk_check.reason}"

        # Step 3b: Launchpad / Uniswap V4 smart route selection (project docs = source of truth)
        buy_plan = await select_buy_route(project, ca_address, trade_amount)
        if not buy_plan.is_executable:
            msg = f"No tradable launchpad route: {buy_plan.reason}"
            logger.warning(
                "buy_blocked_no_route",
                project_id=project.id,
                launchpad=buy_plan.launchpad.value,
                reason=buy_plan.reason,
            )
            return None, None, msg

        # Step 4: Quote validation
        quote = self.quote_engine.get_quote(chain_id, ca_address, trade_amount)
        if not quote.is_valid:
            logger.warning("buy_blocked_invalid_quote", reason=quote.rejection_reason)
            return None, None, f"Quote rejected: {quote.rejection_reason}"

        route_label = f"{buy_plan.launchpad.value}:{buy_plan.route_kind.value}"
        quote.route = route_label

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
            amount_native=(
                self.settings.elysium_buy_amount_hype
                if chain_id in ("elysium", "elysium_testnet") and amount_usd is None
                else None
            ),
            quote_data={
                "expected_tokens": quote.expected_amount_out,
                "slippage_pct": quote.slippage_pct,
                "price_impact_pct": quote.estimated_price_impact_pct,
                "launchpad": buy_plan.launchpad.value,
                "route_kind": buy_plan.route_kind.value,
                "router_contract": buy_plan.router_contract,
                "pool_manager": buy_plan.pool_manager,
                "hook_address": buy_plan.hook_address,
                "quote_asset": buy_plan.quote_asset,
                "buy_amount_hype": (
                    self.settings.elysium_buy_amount_hype
                    if chain_id in ("elysium", "elysium_testnet")
                    else None
                ),
                "gas_strategy": self.settings.evm_gas_strategy,
                "simulation_enabled": self.settings.transaction_simulation_enabled,
            },
            route=route_label,
            status=OrderStatus.SUBMITTED,
            is_paper=self.settings.is_paper or (self.settings.app_env == "development"),
            submitted_at=datetime.now(timezone.utc),
        )
        self._session.add(order)
        await self._session.flush()

        # Step 6: Dispatch to blockchain executor
        if chain_id == "solana":
            exec_res = await self.sol_exec.execute_buy(quote)
        elif is_evm_chain(chain_id):
            exec_res = await self.evm_exec.execute_buy(
                quote,
                router_address=buy_plan.router_contract,
            )
        else:
            msg = f"Unsupported chain for execution: {chain_id}"
            logger.error("buy_blocked_unsupported_chain", chain_id=chain_id)
            order.status = OrderStatus.FAILED
            order.error_message = msg
            assert_valid_transition(project.status, ProjectStatus.SAFETY_BLOCKED, project.id)
            project.status = ProjectStatus.SAFETY_BLOCKED
            await self._session.flush()
            return order, None, msg

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
