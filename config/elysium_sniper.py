"""
config/elysium_sniper.py
───────────────────────
Elysium Ultra-Fast Sniper – authoritative configuration constants.
Mirrors config/ELYSIUM_ULTRA_FAST_SNIPER.md. Settings/env may override
runtime knobs; these values are the documented product defaults.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Tuple

from projects.sniper_config import LaunchpadId


# ── 1. Network ────────────────────────────────────────────────────
NETWORK_NAME = "Elysium Mainnet"
INTERNAL_CHAIN_ID = "elysium"
EVM_CHAIN_ID = 99901
GAS_TOKEN = "HYPE"
EXECUTION_TARGET_MS: Tuple[int, int] = (1000, 5000)  # 1–5 seconds
TRADING_MODE = "automatic_buy"
SELLING_MODE = "manual_only"


# ── 2. Monitoring ─────────────────────────────────────────────────
@dataclass(frozen=True)
class MonitoringConfig:
    x_monitoring: str = "primary"
    telegram_monitoring: str = "secondary"
    x_ca_detection: bool = True
    telegram_ca_detection: bool = True
    admin_verification_required: bool = True
    ignore_normal_member_ca: bool = True
    official_docs_primary_verification: bool = True
    duplicate_ca_detection: bool = True


# ── 3. Buy ────────────────────────────────────────────────────────
@dataclass(frozen=True)
class BuyConfig:
    buy_amount_hype: float = 1.0  # proposed default
    max_slippage_pct: float = 3.0
    max_buy_tax_pct: float = 2.0
    min_liquidity_usd: float = 100.0
    gas_strategy: str = "dynamic_fast"  # market tip × multiplier
    transaction_simulation: bool = True
    honeypot_check: bool = True
    duplicate_purchases: bool = False  # disabled


# ── 4. Launchpads ─────────────────────────────────────────────────
LAUNCHPAD_ADAPTERS: Tuple[LaunchpadId, ...] = (
    LaunchpadId.BOXA,       # native router
    LaunchpadId.KLEOS,      # native router
    LaunchpadId.ELEOS,      # native router
    LaunchpadId.ELYSPUMP,   # native router
    LaunchpadId.ASCEND,     # native router
    LaunchpadId.UNISWAP_V4, # direct swap
)


# ── 5. Selling ────────────────────────────────────────────────────
@dataclass(frozen=True)
class SellConfig:
    automatic_tp: bool = False
    automatic_sl: bool = False
    trailing_stop: bool = False
    automatic_sell: bool = False
    manual_sell: bool = True
    emergency_sell_manual_only: bool = True


# ── 6. Architecture (documented target + Python runtime map) ──────
@dataclass(frozen=True)
class ArchitectureConfig:
    documented_stack: Tuple[str, ...] = (
        "Node.js / TypeScript",
        "viem",
        "WebSocket RPC",
        "Multiple RPC endpoints",
        "Redis",
        "PostgreSQL",
        "Telegram Bot API",
        "X monitoring integration",
    )
    runtime_stack: str = "python"  # this repo
    notes: str = (
        "Python runtime implements the same workflow; "
        "TS/viem remains the documented target architecture."
    )


# ── Aggregated document ───────────────────────────────────────────
@dataclass(frozen=True)
class ElysiumUltraFastSniperConfig:
    network_name: str = NETWORK_NAME
    internal_chain_id: str = INTERNAL_CHAIN_ID
    evm_chain_id: int = EVM_CHAIN_ID
    gas_token: str = GAS_TOKEN
    execution_target_ms: Tuple[int, int] = EXECUTION_TARGET_MS
    trading_mode: str = TRADING_MODE
    selling_mode: str = SELLING_MODE
    monitoring: MonitoringConfig = field(default_factory=MonitoringConfig)
    buy: BuyConfig = field(default_factory=BuyConfig)
    launchpads: Tuple[LaunchpadId, ...] = LAUNCHPAD_ADAPTERS
    sell: SellConfig = field(default_factory=SellConfig)
    architecture: ArchitectureConfig = field(default_factory=ArchitectureConfig)

    def workflow_steps(self) -> List[str]:
        return [
            "Monitor Official X (primary)",
            "Monitor Telegram Admins (secondary)",
            "CA Detected",
            "Verify CA and Source",
            "Identify Launchpad",
            "Check Trading and Liquidity",
            "Select Native Router or Uniswap V4",
            "Simulate Transaction",
            "Automatic Buy",
            "Monitor Confirmation",
            "Track Token – Manual Selling Only",
        ]


ELYSIUM_SNIPER = ElysiumUltraFastSniperConfig()
