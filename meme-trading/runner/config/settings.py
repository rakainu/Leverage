"""Runtime settings loaded from env vars with RUNNER_ prefix."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runner system settings.

    All env vars use the RUNNER_ prefix. Required fields have no default.
    """

    model_config = SettingsConfigDict(
        env_prefix="RUNNER_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Required
    helius_api_key: str
    helius_ws_url: str
    helius_rpc_url: str
    wallets_json_path: str
    weights_yaml_path: str
    db_path: str

    # Optional with defaults
    log_level: str = "INFO"
    enable_executor: bool = True
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    # GMGN/Apify (optional — only needed when gmgn_discovery.enabled=true)
    apify_api_token: str = ""


_cached: Settings | None = None


def get_settings() -> Settings:
    """Return cached Settings singleton."""
    global _cached
    if _cached is None:
        _cached = Settings()
    return _cached


def reset_settings_cache() -> None:
    """Clear the singleton cache — used by tests."""
    global _cached
    _cached = None
