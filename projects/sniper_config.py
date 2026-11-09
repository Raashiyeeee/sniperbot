"""
projects/sniper_config.py
─────────────────────────
Source-of-truth schema for shortlisted Elysium sniper projects.
Docs are loaded from config/shortlist/*.json before CA drop — not inferred
from random social posts.
"""
from __future__ import annotations

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator


class LaunchMechanism(str, Enum):
    BONDING_CURVE = "bonding_curve"
    LIQUIDITY_POOL = "liquidity_pool"
    UNISWAP_V4 = "uniswap_v4"
    UNKNOWN = "unknown"


class LaunchpadId(str, Enum):
    BOXA = "boxa"
    KLEOS = "kleos"
    ELEOS = "eleos"
    ELYSPUMP = "elyspump"
    ASCEND = "ascend"
    UNISWAP_V4 = "uniswap_v4"
    GENERIC_EVM = "generic_evm"


class ProjectSniperConfig(BaseModel):
    """Per-project documentation used as the bot's configuration source of truth."""

    # Identity
    name: str
    ticker: str
    chain_id: str = "elysium"
    slug: Optional[str] = None  # stable file key; defaults to ticker lower

    # Official monitoring sources
    official_x_handle: Optional[str] = None
    official_telegram: Optional[str] = None
    official_telegram_chat_id: Optional[str] = None
    authorized_telegram_admins: List[str] = Field(default_factory=list)

    # Docs / verification
    website_url: Optional[str] = None
    documentation_url: Optional[str] = None

    # Execution configuration
    launchpad: LaunchpadId = LaunchpadId.GENERIC_EVM
    launch_mechanism: LaunchMechanism = LaunchMechanism.UNKNOWN
    router_contract: Optional[str] = None
    pool_manager: Optional[str] = None
    hook_address: Optional[str] = None
    quote_asset: Optional[str] = None  # e.g. native HYPE or USDC address
    token_address: Optional[str] = None  # known CA if pre-published; else filled on detect
    expected_ca_source: str = "both"  # x | telegram | both

    # Ops
    auto_approve: bool = True  # shortlisted projects skip discovery approval
    notes: Optional[str] = None
    enabled: bool = True

    @field_validator("ticker")
    @classmethod
    def _upper_ticker(cls, v: str) -> str:
        return v.strip().upper()

    @field_validator("official_x_handle")
    @classmethod
    def _clean_x(cls, v: Optional[str]) -> Optional[str]:
        if not v:
            return None
        return v.strip().lstrip("@").lower()

    @field_validator("official_telegram")
    @classmethod
    def _clean_tg(cls, v: Optional[str]) -> Optional[str]:
        if not v:
            return None
        return v.strip().lstrip("@").lower()

    @field_validator("authorized_telegram_admins", mode="before")
    @classmethod
    def _admins_as_str(cls, v: object) -> List[str]:
        if v is None:
            return []
        if isinstance(v, list):
            return [str(a).strip() for a in v if str(a).strip()]
        return []

    @property
    def key(self) -> str:
        return (self.slug or self.ticker).strip().lower()
