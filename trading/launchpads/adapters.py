"""
trading/launchpads/adapters.py
──────────────────────────────
Concrete Elysium launchpad adapters (scaffolded).
Live contract calls land here later; paper/dev marks trading live when
router/pool_manager is preconfigured on the project doc.
"""
from __future__ import annotations

from typing import Optional

from database.models import Project
from projects.sniper_config import LaunchMechanism, LaunchpadId
from trading.launchpads.base import BuyPlan, LaunchpadAdapter, RouteKind


def _cfg_router(project: Project) -> Optional[str]:
    return project.router_contract or (project.sniper_config or {}).get("router_contract")


def _cfg_pool_manager(project: Project) -> Optional[str]:
    return project.pool_manager or (project.sniper_config or {}).get("pool_manager")


def _cfg_hook(project: Project) -> Optional[str]:
    return project.hook_address or (project.sniper_config or {}).get("hook_address")


def _cfg_quote(project: Project) -> Optional[str]:
    return project.quote_asset or (project.sniper_config or {}).get("quote_asset")


class _BaseConfiguredAdapter(LaunchpadAdapter):
    """Shared paper-friendly live check: require preconfigured router or pool."""

    async def is_trading_live(self, project: Project, token_address: str) -> bool:
        if not token_address:
            return False
        # Prefer explicit project documentation over discovery
        if _cfg_router(project) or _cfg_pool_manager(project):
            return True
        # Bonding-curve pads can trade without a V4 pool manager
        if self.supports_bonding_curve and (
            (project.launch_mechanism or "") == LaunchMechanism.BONDING_CURVE.value
            or _cfg_router(project)
        ):
            return bool(_cfg_router(project))
        return False

    async def build_buy_plan(
        self,
        project: Project,
        token_address: str,
        amount_usd: float,
    ) -> BuyPlan:
        live = await self.is_trading_live(project, token_address)
        route = self.preferred_route_kind(project)
        return BuyPlan(
            launchpad=self.launchpad_id,
            route_kind=route,
            token_address=token_address,
            router_contract=_cfg_router(project),
            pool_manager=_cfg_pool_manager(project),
            hook_address=_cfg_hook(project),
            quote_asset=_cfg_quote(project),
            trading_live=live,
            reason=(
                f"{self.launchpad_id.value} route={route.value} "
                f"live={live} amount_usd={amount_usd}"
            ),
            metadata={"adapter": self.launchpad_id.value},
        )


class BoxaAdapter(_BaseConfiguredAdapter):
    launchpad_id = LaunchpadId.BOXA
    supports_bonding_curve = True


class KleosAdapter(_BaseConfiguredAdapter):
    launchpad_id = LaunchpadId.KLEOS
    supports_bonding_curve = True


class EleosAdapter(_BaseConfiguredAdapter):
    launchpad_id = LaunchpadId.ELEOS
    supports_bonding_curve = True


class ElysPumpAdapter(_BaseConfiguredAdapter):
    launchpad_id = LaunchpadId.ELYSPUMP
    supports_bonding_curve = True


class AscendAdapter(_BaseConfiguredAdapter):
    launchpad_id = LaunchpadId.ASCEND
    supports_bonding_curve = True
    supports_v4_pool = True


class UniswapV4Adapter(LaunchpadAdapter):
    """Direct Uniswap V4 pool swap — only when a compatible pool exists."""

    launchpad_id = LaunchpadId.UNISWAP_V4
    supports_v4_pool = True

    async def is_trading_live(self, project: Project, token_address: str) -> bool:
        if not token_address:
            return False
        # V4 requires pool manager (+ optional hook) in project docs
        return bool(_cfg_pool_manager(project))

    async def build_buy_plan(
        self,
        project: Project,
        token_address: str,
        amount_usd: float,
    ) -> BuyPlan:
        live = await self.is_trading_live(project, token_address)
        return BuyPlan(
            launchpad=LaunchpadId.UNISWAP_V4,
            route_kind=RouteKind.UNISWAP_V4_POOL,
            token_address=token_address,
            router_contract=_cfg_router(project),
            pool_manager=_cfg_pool_manager(project),
            hook_address=_cfg_hook(project),
            quote_asset=_cfg_quote(project),
            trading_live=live,
            reason=(
                "uniswap_v4 direct pool "
                f"live={live} pool_manager={_cfg_pool_manager(project)} "
                f"amount_usd={amount_usd}"
            ),
            metadata={"adapter": "uniswap_v4"},
        )


class GenericEvmAdapter(_BaseConfiguredAdapter):
    launchpad_id = LaunchpadId.GENERIC_EVM
    supports_bonding_curve = False
    supports_v4_pool = False

    async def is_trading_live(self, project: Project, token_address: str) -> bool:
        # Generic path allowed in paper when CA exists; live still needs docs later
        return bool(token_address)
