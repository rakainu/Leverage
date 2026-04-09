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
    telegram_bot_token: str = Field(alias="TELEGRAM_BOT_TOKEN", default="")
    telegram_chat_id: str = Field(alias="TELEGRAM_CHAT_ID", default="")


class Defaults(BaseModel):
    margin_usdt: float
    leverage: float
    margin_mode: MarginMode
    position_mode: PositionMode
    safety_sl_pct: float
    tp_split: list[float]
    sl_policy: SLPolicyName
    atr_length: int = 14
    atr_timeframe: str = "5m"
    sl_atr_multiplier: float = 3.0
    tp_atr_multipliers: list[float] = Field(default_factory=lambda: [1.0, 2.0, 3.0])
    poll_interval_seconds: int = 10

    @field_validator("tp_split")
    @classmethod
    def _split_sums_to_one(cls, v: list[float]) -> list[float]:
        if len(v) != 3:
            raise ValueError("tp_split must have exactly 3 values")
        if abs(sum(v) - 1.0) > 1e-6:
            raise ValueError(f"tp_split must sum to 1.0, got {sum(v)}")
        return v

    @field_validator("atr_length")
    @classmethod
    def _atr_length_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError(f"atr_length must be positive, got {v}")
        return v

    @field_validator("atr_timeframe")
    @classmethod
    def _atr_timeframe_nonempty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("atr_timeframe must be a non-empty string")
        return v

    @field_validator("sl_atr_multiplier")
    @classmethod
    def _sl_mult_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError(f"sl_atr_multiplier must be positive, got {v}")
        return v

    @field_validator("tp_atr_multipliers")
    @classmethod
    def _tp_mults_valid(cls, v: list[float]) -> list[float]:
        if len(v) != 3:
            raise ValueError(f"tp_atr_multipliers must have exactly 3 values, got {len(v)}")
        if any(x <= 0 for x in v):
            raise ValueError(f"tp_atr_multipliers must all be positive, got {v}")
        if not (v[0] < v[1] < v[2]):
            raise ValueError(f"tp_atr_multipliers must be strictly increasing, got {v}")
        return v

    @field_validator("poll_interval_seconds")
    @classmethod
    def _poll_at_least_one(cls, v: int) -> int:
        if v < 1:
            raise ValueError(f"poll_interval_seconds must be >= 1, got {v}")
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
