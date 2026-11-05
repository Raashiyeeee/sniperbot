"""
config/settings.py
──────────────────
Central settings loaded from environment / .env file.
All risk limits are configurable here — never hardcode them.
"""
from __future__ import annotations

from functools import lru_cache
from typing import List

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
    rapidapi_search_queries: str = '"CA soon" OR "contract dropping soon"'
    min_tweet_date: str = "2026-10-01"

    # ── Google Gemini AI Provider ─────────────────────────────────
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.0-flash"
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
    ca_execution_target_ms: int = 2000

    # ── Risk limits ───────────────────────────────────────────────
    max_buy_tax_pct: float = 3.0
    max_sell_tax_pct: float = 3.0
    min_qualifying_volume_usd: float = 100.0
    per_trade_spend_cap_usd: float = 50.0
    daily_spend_cap_usd: float = 200.0
    daily_loss_cap_usd: float = 150.0
    max_open_positions: int = 3

    # ── EVM (Robinhood Chain) execution defaults ──────────────────
    evm_max_slippage_pct: float = 5.0
    evm_max_price_impact_pct: float = 5.0
    evm_gas_tip_multiplier: float = 1.5      # market rate × 1.5
    evm_max_gas_price_gwei: int = 500
    evm_tx_deadline_seconds: int = 60

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
    def is_live(self) -> bool:
        return self.app_env == "live"

    @property
    def is_paper(self) -> bool:
        return self.app_env == "paper"

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


