"""
trading/launchpads/route_selector.py
────────────────────────────────────
Smart route selector: official CA → launchpad adapter → fastest valid route.
"""
from __future__ import annotations

from typing import List, Optional, Sequence

import structlog

from database.models import Project
from projects.sniper_config import LaunchpadId
from trading.launchpads.base import BuyPlan
from trading.launchpads.registry import get_adapter

logger = structlog.get_logger(__name__)


def _candidate_launchpads(project: Project) -> List[LaunchpadId]:
    """Ordered launchpad attempts from project docs (source of truth).

    A configured dedicated pad is authoritative — do not silently fall back to
    generic_evm when that pad is not tradable yet. Uniswap V4 is added as a
    secondary candidate only when pool_manager is documented and the primary
    pad is Ascend / V4 / unset.
    """
    ordered: List[LaunchpadId] = []
    raw = (project.launchpad or "").strip().lower()
    configured: LaunchpadId | None = None
    if raw:
        try:
            configured = LaunchpadId(raw)
            ordered.append(configured)
        except ValueError:
            logger.warning("route_selector.unknown_launchpad", launchpad=raw)

    allow_v4_fallback = configured in (
        None,
        LaunchpadId.ASCEND,
        LaunchpadId.UNISWAP_V4,
        LaunchpadId.GENERIC_EVM,
    )
    if (
        project.pool_manager
        and allow_v4_fallback
        and LaunchpadId.UNISWAP_V4 not in ordered
    ):
        ordered.append(LaunchpadId.UNISWAP_V4)

    if not ordered:
        ordered.append(LaunchpadId.GENERIC_EVM)
    return ordered


class SmartRouteSelector:
    """Pick the first executable BuyPlan across candidate adapters."""

    def __init__(self, candidates: Optional[Sequence[LaunchpadId]] = None) -> None:
        self._override_candidates = list(candidates) if candidates else None

    async def select(
        self,
        project: Project,
        token_address: str,
        amount_usd: float,
    ) -> BuyPlan:
        if not token_address:
            return BuyPlan(
                launchpad=LaunchpadId.GENERIC_EVM,
                route_kind=get_adapter(LaunchpadId.GENERIC_EVM).preferred_route_kind(project),
                token_address="",
                trading_live=False,
                reason="missing token address",
            )

        candidates = self._override_candidates or _candidate_launchpads(project)
        last_plan: Optional[BuyPlan] = None

        for launchpad in candidates:
            adapter = get_adapter(launchpad)
            plan = await adapter.build_buy_plan(project, token_address, amount_usd)
            last_plan = plan
            if plan.is_executable:
                logger.info(
                    "route_selector.selected",
                    project_id=project.id,
                    launchpad=plan.launchpad.value,
                    route=plan.route_kind.value,
                    reason=plan.reason,
                )
                return plan
            logger.debug(
                "route_selector.candidate_not_live",
                launchpad=launchpad.value,
                reason=plan.reason,
            )

        # Nothing tradable yet — return last attempt for diagnostics
        if last_plan is not None:
            last_plan.reason = f"no tradable route among {[c.value for c in candidates]}: {last_plan.reason}"
            return last_plan

        return BuyPlan(
            launchpad=LaunchpadId.GENERIC_EVM,
            route_kind=get_adapter(LaunchpadId.GENERIC_EVM).preferred_route_kind(project),
            token_address=token_address,
            trading_live=False,
            reason="no launchpad candidates",
        )


async def select_buy_route(
    project: Project,
    token_address: str,
    amount_usd: float,
) -> BuyPlan:
    """Module-level helper used by the buy engine."""
    return await SmartRouteSelector().select(project, token_address, amount_usd)
