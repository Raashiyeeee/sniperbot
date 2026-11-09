"""
trading/emergency.py
────────────────────
Global Emergency Stop, Per-Chain Pause, and Project Controls (Spec v3.1 §12.2).
Provides thread-safe circuit breakers to halt trading immediately.
"""
from __future__ import annotations

from typing import Set

import structlog

logger = structlog.get_logger(__name__)


class EmergencyManager:
    """Manages system-wide and targeted trading circuit breakers."""

    _instance = None

    def __new__(cls) -> EmergencyManager:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._global_stop = False
            cls._instance._paused_chains: Set[str] = set()
            cls._instance._cancelled_projects: Set[str] = set()
        return cls._instance

    @property
    def is_global_stop_active(self) -> bool:
        return self._global_stop

    def trigger_global_stop(self, actor: str = "operator") -> None:
        """Trigger an immediate global trading halt across all chains and projects."""
        self._global_stop = True
        logger.critical("GLOBAL_EMERGENCY_STOP_TRIGGERED", actor=actor)

    def resume_global_trading(self, actor: str = "operator") -> None:
        """Resume normal trading operations after explicit operator action."""
        self._global_stop = False
        logger.warning("GLOBAL_EMERGENCY_STOP_CLEARED", actor=actor)

    def pause_chain(self, chain_id: str, actor: str = "operator") -> None:
        """Pause automated trading on a specific blockchain (e.g. 'robinhood', 'solana', or 'elysium')."""
        self._paused_chains.add(chain_id.lower())
        logger.warning("chain_trading_paused", chain=chain_id, actor=actor)

    def resume_chain(self, chain_id: str, actor: str = "operator") -> None:
        """Resume automated trading on a paused blockchain."""
        self._paused_chains.discard(chain_id.lower())
        logger.info("chain_trading_resumed", chain=chain_id, actor=actor)

    def is_chain_paused(self, chain_id: str) -> bool:
        return chain_id.lower() in self._paused_chains

    def cancel_project(self, project_id: str, actor: str = "operator") -> None:
        """Cancel and block all trading for a specific project."""
        self._cancelled_projects.add(project_id)
        logger.warning("project_trading_cancelled", project_id=project_id, actor=actor)

    def is_project_cancelled(self, project_id: str) -> bool:
        return project_id in self._cancelled_projects

    def can_trade(self, chain_id: str, project_id: str) -> tuple[bool, str]:
        """Check all circuit breakers before allowing transaction submission."""
        if self._global_stop:
            return False, "Global emergency stop is active. Trading blocked."
        if self.is_chain_paused(chain_id):
            return False, f"Trading is paused for chain {chain_id}."
        if self.is_project_cancelled(project_id):
            return False, f"Project {project_id} monitoring has been cancelled."
        return True, "Trading allowed"
