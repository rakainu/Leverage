"""Runtime configuration: pydantic-settings + YAML."""
from __future__ import annotations
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


SLPolicyName = Literal["p1_breakeven", "p2_step_stop", "p3_trail", "p4_hybrid"]
MarginMode = Literal["isolated", "cross"]
PositionMode = Literal["net", "long_short"]


class _RawBloFinEnv(BaseSettings):
    """Raw env vars for both demo and live BloFin credentials."""
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore",
    )
    live_api_key: str = Field(alias="BLOFIN_LIVE_API_KEY", default="")
    live_api_secret: str = Field(alias="BLOFIN_LIVE_API_SECRET", default="")
    live_passphrase: str = Field(alias="BLOFIN_LIVE_PASSPHRASE", default="")
    demo_api_key: str = Field(alias="BLOFIN_DEMO_API_KEY", default="")
    demo_api_secret: str = Field(alias="BLOFIN_DEMO_API_SECRET", default="")
    demo_passphrase: str = Field(alias="BLOFIN_DEMO_PASSPHRASE", default="")
    env: Literal["demo", "live"] = Field(alias="BLOFIN_ENV", default="demo")


class BloFinCreds(BaseModel):
    """Resolved credentials — picks demo vs live based on env."""
    api_key: str
    api_secret: str
    passphrase: str
    env: Literal["demo", "live"]

    @classmethod
    def from_environment(cls) -> "BloFinCreds":
        raw = _RawBloFinEnv()
        if raw.env == "demo":
            if not (raw.demo_api_key and raw.demo_api_secret and raw.demo_passphrase):
                raise ValueError(
                    "BLOFIN_ENV=demo requires BLOFIN_DEMO_API_KEY / "
                    "BLOFIN_DEMO_API_SECRET / BLOFIN_DEMO_PASSPHRASE to be set"
                )
            return cls(
                api_key=raw.demo_api_key, api_secret=raw.demo_api_secret,
                passphrase=raw.demo_passphrase, env="demo",
            )
        if not (raw.live_api_key and raw.live_api_secret and raw.live_passphrase):
            raise ValueError(
                "BLOFIN_ENV=live requires BLOFIN_LIVE_API_KEY / "
                "BLOFIN_LIVE_API_SECRET / BLOFIN_LIVE_PASSPHRASE to be set"
            )
        return cls(
            api_key=raw.live_api_key, api_secret=raw.live_api_secret,
            passphrase=raw.live_passphrase, env="live",
        )


class BridgeCreds(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore",
    )
    shared_secret: str = Field(alias="BRIDGE_SECRET")
    audit_secret: str = Field(alias="BRIDGE_AUDIT_SECRET", default="")
    telegram_bot_token: str = Field(alias="TELEGRAM_BOT_TOKEN", default="")
    telegram_chat_id: str = Field(alias="TELEGRAM_CHAT_ID", default="")
    telegram_allowed_user_id: int = Field(
        alias="TG_ALLOWED_USER_ID", default=6421609315,
    )


class Defaults(BaseModel):
    margin_usdt: float
    leverage: float
    margin_mode: MarginMode
    position_mode: PositionMode
    sl_policy: SLPolicyName
    # --- SL / Trail / TP ---
    sl_loss_usdt: float = 13.0
    breakeven_usdt: float = 15.0
    lock_profit_activate_usdt: float = 20.0
    lock_profit_usdt: float = 15.0
    trail_activate_usdt: float = 25.0
    trail_start_usdt: float = 30.0
    trail_distance_usdt: float = 10.0
    tp_limit_margin_pct: float = 2.0
    # --- EMA retest entry ---
    ema_retest_period: int = 9
    ema_retest_timeframe: str = "5m"
    ema_retest_timeout_minutes: int = 30
    ema_retest_max_overshoot_pct: float = 0.2
    # Minimum |EMA(9) slope-over-3-bars| in % for a fill to release.
    # 0 disables the gate. Defaults to 0.03 — historical 14-day analysis
    # showed every trade with |slope| < 0.03% lost (0/14 WR, -$197).
    min_5m_slope_pct: float = 0.03
    poll_interval_seconds: int = 10

    @field_validator("sl_loss_usdt", "breakeven_usdt", "lock_profit_activate_usdt", "lock_profit_usdt", "trail_activate_usdt", "trail_start_usdt", "trail_distance_usdt")
    @classmethod
    def _positive_dollar(cls, v: float) -> float:
        if v <= 0:
            raise ValueError(f"must be positive, got {v}")
        return v

    @field_validator("tp_limit_margin_pct")
    @classmethod
    def _tp_limit_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError(f"tp_limit_margin_pct must be positive, got {v}")
        return v

    @field_validator("poll_interval_seconds")
    @classmethod
    def _poll_at_least_one(cls, v: int) -> int:
        if v < 1:
            raise ValueError(f"poll_interval_seconds must be >= 1, got {v}")
        return v

    @field_validator("min_5m_slope_pct")
    @classmethod
    def _slope_non_negative(cls, v: float) -> float:
        if v < 0:
            raise ValueError(f"min_5m_slope_pct must be >= 0, got {v}")
        return v


class SymbolConfig(BaseModel):
    enabled: bool
    margin_usdt: float
    leverage: float
    margin_mode: MarginMode
    sl_policy: SLPolicyName


class Settings(BaseModel):
    blofin: BloFinCreds
    bridge: BridgeCreds
    defaults: Defaults
    symbols: dict[str, SymbolConfig]


def load_config(yaml_path: Path) -> Settings:
    raw = yaml.safe_load(yaml_path.read_text())
    return Settings(
        blofin=BloFinCreds.from_environment(),
        bridge=BridgeCreds(),
        defaults=Defaults(**raw["defaults"]),
        symbols={
            name: SymbolConfig(**body)
            for name, body in (raw.get("symbols") or {}).items()
        },
    )
