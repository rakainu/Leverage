"""Runtime configuration. Two layers: environment (secrets, infra URLs) + weights.yaml (tunables)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Loaded from environment / .env. Secrets and infra wiring only."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    hlsm_config: Path = Path("/app/config/weights.yaml")
    hlsm_pg_url: str = "postgresql+psycopg://hlsm:hlsm_local_dev@localhost:5432/hlsm"
    hlsm_redis_url: str = "redis://localhost:6379/0"

    # BloFin (the only venue in MVP; sub-account: Trials)
    blofin_env: str = "demo"  # demo | live
    blofin_api_key: str = ""
    blofin_api_secret: str = ""
    blofin_api_passphrase: str = ""
    blofin_demo_rest_url: str = "https://demo-trading-openapi.blofin.com"
    blofin_demo_ws_url: str = "wss://demo-trading-openapi.blofin.com/ws/public"
    blofin_subaccount_label: str = "Trials"

    # Telegram (bot token via env; chat_id may be a channel)
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # Dashboard host
    hlsm_dashboard_host: str = "hlsm.agentneo.cloud"
    hlsm_api_port: int = 8788

    # Misc
    log_level: str = "INFO"


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def load_weights(path: Path | None = None) -> dict[str, Any]:
    """Load weights.yaml. Pure function; safe to call repeatedly for hot-reload."""
    target = Path(path) if path else get_settings().hlsm_config
    with open(target, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data
