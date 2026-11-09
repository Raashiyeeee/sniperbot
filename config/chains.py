"""
config/chains.py
────────────────
Chain definitions for supported networks.
Supported: Robinhood Chain (EVM L2), Solana, Elysium (mainnet + testnet).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Dict, Optional


class ChainType(str, Enum):
    EVM = "evm"
    SOLANA = "solana"


@dataclass(frozen=True)
class ChainConfig:
    chain_id: str                   # internal identifier
    display_name: str
    chain_type: ChainType
    native_token: str
    rpc_url: str = ""               # set via env in production
    explorer_url: str = ""
    address_prefix: Optional[str] = None   # e.g. "0x" for EVM
    evm_chain_id: Optional[int] = None     # numeric EVM chain id when applicable


CHAINS: Dict[str, ChainConfig] = {
    "robinhood": ChainConfig(
        chain_id="robinhood",
        display_name="Robinhood Chain",
        chain_type=ChainType.EVM,
        native_token="ETH",
        explorer_url="https://explorer.robinhood.com",   # placeholder
        address_prefix="0x",
    ),
    "solana": ChainConfig(
        chain_id="solana",
        display_name="Solana",
        chain_type=ChainType.SOLANA,
        native_token="SOL",
        explorer_url="https://solscan.io",
    ),
    "elysium": ChainConfig(
        chain_id="elysium",
        display_name="Elysium Mainnet",
        chain_type=ChainType.EVM,
        native_token="HYPE",
        rpc_url="https://rpc.elysp.com",
        explorer_url="https://elysiumexplore.com",
        address_prefix="0x",
        evm_chain_id=99901,
    ),
    "elysium_testnet": ChainConfig(
        chain_id="elysium_testnet",
        display_name="Elysium Testnet",
        chain_type=ChainType.EVM,
        native_token="HYPE",
        rpc_url="https://testnet-rpc.elysium.kinetiq.xyz",
        explorer_url="https://explorer-elysium-testnet.t.conduit.xyz",
        address_prefix="0x",
        evm_chain_id=99801,
    ),
}


def get_chain(chain_id: str) -> Optional[ChainConfig]:
    return CHAINS.get(chain_id.lower())


def resolve_rpc_url(chain_id: str) -> str:
    """Return RPC URL, preferring env/settings overrides for Elysium."""
    chain = get_chain(chain_id)
    if chain is None:
        return ""
    try:
        from config.settings import get_settings
        settings = get_settings()
        if chain_id.lower() == "elysium" and settings.elysium_rpc_url:
            return settings.elysium_rpc_url
        if chain_id.lower() == "elysium_testnet" and settings.elysium_testnet_rpc_url:
            return settings.elysium_testnet_rpc_url
    except Exception:  # noqa: BLE001
        pass
    return chain.rpc_url


def is_evm_chain(chain_id: str) -> bool:
    chain = get_chain(chain_id)
    return chain is not None and chain.chain_type == ChainType.EVM


def supported_chain_ids() -> list[str]:
    return list(CHAINS.keys())


def is_valid_evm_address(address: str) -> bool:
    import re
    return bool(re.match(r"^0x[0-9a-fA-F]{40}$", address))


def is_valid_solana_address(address: str) -> bool:
    import re
    # Base58, 32-44 chars
    return bool(re.match(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$", address))


def validate_address(address: str, chain_id: str) -> bool:
    chain = get_chain(chain_id)
    if chain is None:
        return False
    if chain.chain_type == ChainType.EVM:
        return is_valid_evm_address(address)
    if chain.chain_type == ChainType.SOLANA:
        return is_valid_solana_address(address)
    return False
