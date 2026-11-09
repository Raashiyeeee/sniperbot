"""
trading/launchpads/base.py
──────────────────────────
Shared interface for Elysium launchpad / Uniswap V4 buy adapters.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional

from database.models import Project
from projects.sniper_config import LaunchMechanism, LaunchpadId


class RouteKind(str, Enum):
    NATIVE_ROUTER = "native_router"
    BONDING_CURVE = "bonding_curve"
    UNISWAP_V4_POOL = "uniswap_v4_pool"
    GENERIC_EVM = "generic_evm"


@dataclass
class BuyPlan:
    """Selected execution plan for a detected CA."""

    launchpad: LaunchpadId
    route_kind: RouteKind
    token_address: str
    router_contract: Optional[str] = None
    pool_manager: Optional[str] = None
    hook_address: Optional[str] = None
    quote_asset: Optional[str] = None
    trading_live: bool = False
    reason: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_executable(self) -> bool:
        return bool(self.token_address) and self.trading_live


class LaunchpadAdapter(ABC):
    """Dedicated buy adapter for one launchpad or Uniswap V4."""

    launchpad_id: LaunchpadId
    supports_bonding_curve: bool = False
    supports_v4_pool: bool = False

    @abstractmethod
    async def is_trading_live(self, project: Project, token_address: str) -> bool:
        """Return True when the launchpad/pool is tradable for this token."""

    @abstractmethod
    async def build_buy_plan(
        self,
        project: Project,
        token_address: str,
        amount_usd: float,
    ) -> BuyPlan:
        """Build the fastest valid buy route for this adapter."""

    def preferred_route_kind(self, project: Project) -> RouteKind:
        mechanism = (project.launch_mechanism or "").lower()
        if mechanism == LaunchMechanism.BONDING_CURVE.value and self.supports_bonding_curve:
            return RouteKind.BONDING_CURVE
        if mechanism == LaunchMechanism.UNISWAP_V4.value and self.supports_v4_pool:
            return RouteKind.UNISWAP_V4_POOL
        if self.supports_bonding_curve:
            return RouteKind.BONDING_CURVE
        if self.supports_v4_pool:
            return RouteKind.UNISWAP_V4_POOL
        return RouteKind.NATIVE_ROUTER
