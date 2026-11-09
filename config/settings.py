"""
config/settings.py
──────────────────
Central settings loaded from environment / .env file.
All risk limits are configurable here — never hardcode them.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any, List, Optional

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Application ───────────────────────────────────────────────
    app_env: str = "development"          # development | paper | live
    log_level: str = "INFO"
    operator_timezone: str = "UTC"

    # ── Database ──────────────────────────────────────────────────
    database_url: str = "postgresql+asyncpg://radar:changeme@localhost:5432/radar_db"
    database_url_sync: str = "postgresql://radar:changeme@localhost:5432/radar_db"

    # ── Redis ─────────────────────────────────────────────────────
    redis_url: str = "redis://localhost:6379/0"

    # ── Telegram ──────────────────────────────────────────────────
    telegram_bot_token: str = ""
    telegram_operator_ids: str = ""          # comma-separated
    telegram_alert_chat_id: str = ""
    telegram_api_id: Optional[int] = None
    telegram_api_hash: str = ""
    telegram_session_string: str = ""
    telegram_userbot_chats: str = ""         # comma-separated chat IDs, usernames, or group titles

    # ── X / Twitter ───────────────────────────────────────────────
    x_bearer_token: str = ""
    x_api_key: str = ""
    x_api_secret: str = ""
    x_access_token: str = ""
    x_access_token_secret: str = ""

    # ── RapidAPI Twitter/X ─────────────────────────────────────────
    rapidapi_key: str = ""
    rapidapi_twitter_host: str = "twitter-api45.p.rapidapi.com"
    rapidapi_poll_interval_seconds: float = 300.0
    # Faster cadence for approved official handles (target CA→buy within seconds)
    rapidapi_approved_poll_interval_seconds: float = 3.0
    rapidapi_search_queries: str = '"CA soon" OR "contract dropping soon"'
    min_tweet_date: str = "2026-10-01"

    # ── Google Gemini AI Provider ─────────────────────────────────
    gemini_api_key: str = ""
    gemini_model: str = "gemini-flash-lite-latest"
    gemini_enabled: bool = True
    gemini_timeout: float = 30.0
    gemini_max_retries: int = 3
    gemini_max_daily_requests: int = 1000

    # ── AI Scoring Weights & Thresholds ────────────────────────────
    ai_min_project_score: float = 80.0
    ai_utility_weight: float = 25.0
    ai_credibility_weight: float = 20.0
    ai_community_weight: float = 15.0
    ai_x_account_weight: float = 15.0
    ai_launch_confirmation_weight: float = 15.0
    ai_ecosystem_weight: float = 10.0

    # ── Trading & Verification Gates ──────────────────────────────
    require_manual_project_approval: bool = True
    require_verified_official_ca: bool = True
    enable_live_trading: bool = False
    # Target mid-range of the 1–5s Ultra-Fast Sniper window
    ca_execution_target_ms: int = 2000
    ca_execution_target_min_ms: int = 1000
    ca_execution_target_max_ms: int = 5000

    # ── Wallet (EVM / Elysium) ────────────────────────────────────
    # Set EVM_PRIVATE_KEY in .env — NEVER commit the real key.
    # Leave blank to remain in paper/simulation mode.
    evm_private_key: str = ""

    # ── Shortlist preload (Elysium sniper project docs) ───────────
    # Directory of *.json project configs (see config/shortlist/_template.json)
    shortlist_dir: str = "config/shortlist"
    shortlist_preload_on_start: bool = True

    # Default sniper chain (Ultra-Fast Sniper config: 'elysium_testnet' or 'elysium')
    default_sniper_chain_id: str = "elysium"

    # ── Monitoring (Elysium Ultra-Fast Sniper §2) ─────────────────
    x_ca_detection_enabled: bool = True
    telegram_ca_detection_enabled: bool = True
    telegram_admin_verification_required: bool = True
    ignore_non_admin_telegram_ca: bool = True
    duplicate_ca_detection_enabled: bool = True

    # ── Risk / buy limits (Elysium Ultra-Fast Sniper §3) ──────────
    # Buy amount in HYPE per trade (configured to 0.05 HYPE)
    elysium_buy_amount_hype: float = 0.05
    hype_usd_price: float = 1.0  # used to map HYPE buys into USD risk caps
    max_buy_tax_pct: float = 3.0
    max_sell_tax_pct: float = 3.0
    min_qualifying_volume_usd: float = 100.0  # minimum liquidity / genuine volume
    per_trade_spend_cap_usd: float = 50.0
    daily_spend_cap_usd: float = 200.0
    daily_loss_cap_usd: float = 150.0
    max_open_positions: int = 3
    transaction_simulation_enabled: bool = True
    honeypot_check_enabled: bool = True
    allow_duplicate_purchases: bool = False  # Disabled

    # ── Selling (Elysium Ultra-Fast Sniper §5) ────────────────────
    # All automatic exits OFF — manual sell / emergency only
    automatic_take_profit: bool = False
    automatic_stop_loss: bool = False
    trailing_stop: bool = False
    automatic_sell: bool = False
    manual_sell_enabled: bool = True
    emergency_sell_manual_only: bool = True

    # ── EVM (Robinhood / Elysium) execution defaults ──────────────
    evm_max_slippage_pct: float = 5.0
    evm_max_price_impact_pct: float = 5.0
    # Gas strategy: Dynamic / Fast → tip multiplier on market base
    evm_gas_strategy: str = "dynamic_fast"
    evm_gas_tip_multiplier: float = 1.5      # market rate × 1.5
    evm_max_gas_price_gwei: int = 500
    evm_tx_deadline_seconds: int = 60

    # ── Elysium RPC endpoints (HTTP + WS, multi-endpoint) ─────────
    elysium_rpc_url: str = "https://rpc.elysp.com"
    elysium_rpc_fallback_urls: str = ""  # comma-separated extra HTTP RPCs
    elysium_ws_rpc_url: str = ""         # WebSocket RPC for fast confirmations
    elysium_testnet_rpc_url: str = "https://testnet-rpc.elysium.kinetiq.xyz"

    # ── Solana execution defaults ─────────────────────────────────
    sol_max_slippage_pct: float = 5.0
    sol_max_priority_fee_sol: float = 0.01
    sol_tx_deadline_seconds: int = 30

    # ── Timing ────────────────────────────────────────────────────
    ca_delay_timeout_hours: int = 4
    ca_delay_no_launch_time_hours: int = 24
    pending_approval_expiry_hours: int = 48

    # ── Volume wash-trade filter ──────────────────────────────────
    wash_trade_round_trip_seconds: int = 300   # 5 minutes

    # ── Derived helpers ───────────────────────────────────────────
    @property
    def operator_ids(self) -> List[int]:
        """Parse comma-separated operator Telegram user IDs."""
        if not self.telegram_operator_ids:
            return []
        return [int(x.strip()) for x in self.telegram_operator_ids.split(",") if x.strip()]

    @property
    def elysium_rpc_urls(self) -> List[str]:
        """Primary + fallback HTTP RPC endpoints for Elysium mainnet."""
        urls = [self.elysium_rpc_url] if self.elysium_rpc_url else []
        if self.elysium_rpc_fallback_urls:
            urls.extend(
                u.strip() for u in self.elysium_rpc_fallback_urls.split(",") if u.strip()
            )
        # Deduplicate preserving order
        seen = set()
        out: List[str] = []
        for u in urls:
            if u not in seen:
                seen.add(u)
                out.append(u)
        return out

    @property
    def elysium_buy_amount_usd(self) -> float:
        """Map configured 1 HYPE buy into USD risk accounting."""
        return float(self.elysium_buy_amount_hype) * float(self.hype_usd_price)

    @property
    def is_live(self) -> bool:
        return self.app_env == "live"

    @property
    def is_paper(self) -> bool:
        return self.app_env == "paper"

    @field_validator("telegram_api_id", mode="before")
    @classmethod
    def parse_telegram_api_id(cls, v: Any) -> Optional[int]:
        if v is None or v == "" or (isinstance(v, str) and not v.strip()):
            return None
        return int(v)

    @field_validator("app_env")
    @classmethod
    def validate_env(cls, v: str) -> str:
        allowed = {"development", "paper", "live"}
        if v not in allowed:
            raise ValueError(f"app_env must be one of {allowed}")
        return v


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


# Convenient module-level alias
settings = get_settings()


