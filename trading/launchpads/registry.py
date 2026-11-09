"""
trading/launchpads/registry.py
──────────────────────────────
Launchpad adapter registry.
"""
from __future__ import annotations

from typing import Dict, List

from projects.sniper_config import LaunchpadId
from trading.launchpads.adapters import (
    AscendAdapter,
    BoxaAdapter,
    EleosAdapter,
    ElysPumpAdapter,
    GenericEvmAdapter,
    KleosAdapter,
    UniswapV4Adapter,
)
from trading.launchpads.base import LaunchpadAdapter

_ADAPTERS: Dict[LaunchpadId, LaunchpadAdapter] = {
    LaunchpadId.BOXA: BoxaAdapter(),
    LaunchpadId.KLEOS: KleosAdapter(),
    LaunchpadId.ELEOS: EleosAdapter(),
    LaunchpadId.ELYSPUMP: ElysPumpAdapter(),
    LaunchpadId.ASCEND: AscendAdapter(),
    LaunchpadId.UNISWAP_V4: UniswapV4Adapter(),
    LaunchpadId.GENERIC_EVM: GenericEvmAdapter(),
}


def get_adapter(launchpad: LaunchpadId | str) -> LaunchpadAdapter:
    if isinstance(launchpad, str):
        try:
            launchpad = LaunchpadId(launchpad.lower())
        except ValueError:
            launchpad = LaunchpadId.GENERIC_EVM
    return _ADAPTERS.get(launchpad, _ADAPTERS[LaunchpadId.GENERIC_EVM])


def list_adapters() -> List[LaunchpadId]:
    return list(_ADAPTERS.keys())
