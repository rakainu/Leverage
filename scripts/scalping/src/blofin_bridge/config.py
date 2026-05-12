"""Runtime configuration: pydantic-settings + YAML.

Design notes — multi-symbol scaling
-----------------------------------
The strategy DNA (SL/BE/trail dollar thresholds) lives in `defaults`. Each
symbol picks its own `margin_usdt`. At load time we build a
`ResolvedSymbolConfig` per enabled symbol by **auto-scaling every dollar
threshold by `(symbol_margin / default_margin)`**. So:

  defaults.margin_usdt = 100, defaults.sl_loss_usdt = 13
  ZEC.margin_usdt = 250        → ZEC effective sl_loss_usdt = 32.50
  SOL.margin_usdt = 30         → SOL effective sl_loss_usdt = 3.90

That keeps the percent-risk profile constant across symbols and makes adding
a new token a one-line change. Any symbol can still override an individual
threshold explicitly (e.g. SOL wants tighter SL than the scaled value) by
setting the field directly under that symbol.
"""
from __future__ import annotations
from pathlib import Path
from typing import Literal, Optional

import yaml
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


SLPolicyName = Literal["p1_breakeven", "p2_step_stop", "p3_trail", "p4_hybrid"]
MarginMode = Literal["isolated", "cross"]
PositionMode = Literal["net", "long_short"]


# Names of all dollar thresholds that auto-scale with margin.
_SCALED_DOLLAR_FIELDS: tuple[str, ...] = (
    "sl_loss_usdt",
    "breakeven_usdt",
    "lock_profit_activate_usdt",
    "lock_profit_usdt",
    "trail_activate_usdt",
    "trail_start_usdt",
    "trail_distance_usdt",
)


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
    """Strategy DNA — the dollar thresholds for the *baseline* margin.

    All `*_usdt` fields below are scaled per-symbol by
    (symbol_margin / margin_usdt) when ResolvedSymbolConfig is built.
    """
    margin_usdt: float
    leverage: float
    margin_mode: MarginMode
    position_mode: PositionMode
    sl_policy: SLPolicyName
    # --- SL / Trail / TP (in $ at baseline margin; auto-scale per symbol) ---
    sl_loss_usdt: float = 13.0
    breakeven_usdt: float = 15.0
    lock_profit_activate_usdt: float = 20.0
    lock_profit_usdt: float = 15.0
    trail_activate_usdt: float = 25.0
    trail_start_usdt: float = 30.0
    trail_distance_usdt: float = 10.0
    tp_limit_margin_pct: float = 2.0
    # --- EMA retest entry (symbol-agnostic) ---
    ema_retest_period: int = 9
    ema_retest_timeframe: str = "5m"
    ema_retest_timeout_minutes: int = 30
    ema_retest_max_overshoot_pct: float = 0.2
    # Minimum |EMA(9) slope-over-3-bars| in % for a fill to release.
    # 0 disables the gate. Historical 14-day audit (137 trades) showed every
    # trade with |slope| < 0.03% lost (0/14 WR, -$197).
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
    """Per-symbol *raw* YAML config.

    Required fields define sizing and policy choice. The `*_usdt` fields are
    optional — when absent, ResolvedSymbolConfig fills them by scaling
    defaults. When present, the override wins.
    """
    enabled: bool
    margin_usdt: float
    leverage: float
    margin_mode: MarginMode
    sl_policy: SLPolicyName
    # Optional per-symbol dollar overrides — leave unset to auto-scale.
    sl_loss_usdt: Optional[float] = None
    breakeven_usdt: Optional[float] = None
    lock_profit_activate_usdt: Optional[float] = None
    lock_profit_usdt: Optional[float] = None
    trail_activate_usdt: Optional[float] = None
    trail_start_usdt: Optional[float] = None
    trail_distance_usdt: Optional[float] = None
    tp_limit_margin_pct: Optional[float] = None


class ResolvedSymbolConfig(BaseModel):
    """Per-symbol *effective* config — scaled / fully resolved.

    Every dollar threshold has been filled in. Poller and entry handler read
    from this — no scaling math at runtime.
    """
    enabled: bool
    margin_usdt: float
    leverage: float
    margin_mode: MarginMode
    sl_policy: SLPolicyName
    sl_loss_usdt: float
    breakeven_usdt: float
    lock_profit_activate_usdt: float
    lock_profit_usdt: float
    trail_activate_usdt: float
    trail_start_usdt: float
    trail_distance_usdt: float
    tp_limit_margin_pct: float

    @classmethod
    def from_symbol_and_defaults(
        cls, sym: SymbolConfig, defaults: Defaults,
    ) -> "ResolvedSymbolConfig":
        """Build effective config by scaling defaults to this symbol's margin.

        For each $-threshold, use the symbol's explicit override if set,
        otherwise scale defaults × (sym.margin_usdt / defaults.margin_usdt).
        """
        if defaults.margin_usdt <= 0:
            raise ValueError("defaults.margin_usdt must be positive")
        scale = sym.margin_usdt / defaults.margin_usdt

        resolved: dict[str, float] = {}
        for field in _SCALED_DOLLAR_FIELDS:
            override = getattr(sym, field)
            base = getattr(defaults, field)
            resolved[field] = float(override) if override is not None else base * scale

        tp_limit = (
            sym.tp_limit_margin_pct
            if sym.tp_limit_margin_pct is not None
            else defaults.tp_limit_margin_pct
        )

        return cls(
            enabled=sym.enabled,
            margin_usdt=sym.margin_usdt,
            leverage=sym.leverage,
            margin_mode=sym.margin_mode,
            sl_policy=sym.sl_policy,
            tp_limit_margin_pct=tp_limit,
            **resolved,
        )


class Settings(BaseModel):
    blofin: BloFinCreds
    bridge: BridgeCreds
    defaults: Defaults
    symbols: dict[str, ResolvedSymbolConfig]


def load_config(yaml_path: Path) -> Settings:
    raw = yaml.safe_load(yaml_path.read_text())
    defaults = Defaults(**raw["defaults"])
    raw_symbols = {
        name: SymbolConfig(**body)
        for name, body in (raw.get("symbols") or {}).items()
    }
    resolved_symbols = {
        name: ResolvedSymbolConfig.from_symbol_and_defaults(sym, defaults)
        for name, sym in raw_symbols.items()
    }
    return Settings(
        blofin=BloFinCreds.from_environment(),
        bridge=BridgeCreds(),
        defaults=defaults,
        symbols=resolved_symbols,
    )
