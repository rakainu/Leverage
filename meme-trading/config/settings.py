"""SMC Trading System configuration via environment variables."""

import json
from pydantic_settings import BaseSettings
from pydantic import field_validator


class Settings(BaseSettings):
    # Solana RPC
    solana_rpc_urls: list[str] = ["https://api.mainnet-beta.solana.com"]
    solana_ws_urls: list[str] = ["wss://api.mainnet-beta.solana.com"]
    solana_private_key: str = ""

    # Wallet
    solana_wallet_pubkey: str = ""

    # Helius
    helius_api_key: str = ""

    # Birdeye
    birdeye_api_key: str = ""

    # Nansen
    nansen_api_key: str = ""

    # Apify (GMGN scrapers)
    apify_api_token: str = ""

    # Trading mode
    mode: str = "paper"  # "paper" | "live"

    # Convergence parameters
    convergence_window_minutes: int = 60
    convergence_threshold: int = 3

    # Position management
    max_concurrent_positions: int = 5
    trade_amount_sol: float = 0.1
    stop_loss_pct: float = 25.0
    position_timeout_minutes: int = 240

    # Trailing stop loss
    # Phase 1: Entry SL at -stop_loss_pct (25%)
    # Phase 2: Once profit hits trail_activate_pct, move SL to trail_breakeven_pct
    # Phase 3: Trail at trail_distance_pct below high watermark
    trail_activate_pct: float = 30.0
    trail_breakeven_pct: float = 5.0
    trail_distance_pct: float = 20.0

    # Convergence speed filter (minutes between first buy and signal)
    min_convergence_minutes: float = 10.0
    max_convergence_minutes: float = 20.0

    # Time-of-day filter (UTC hours to block trading)
    blocked_hours_utc: list[int] = [13, 14, 15, 16]

    # Safety thresholds
    min_liquidity_sol: float = 10.0
    max_token_age_hours: float = 72.0
    require_lp_lock: bool = True
    require_no_mint_authority: bool = True
    max_top_holder_pct: float = 30.0
    honeypot_max_tax_pct: float = 10.0

    # Jupiter
    jupiter_api_key: str = ""
    slippage_bps: int = 300

    # Dashboard
    dashboard_host: str = "0.0.0.0"
    dashboard_port: int = 8420

    # Telegram
    telegram_bot_token: str = ""
    telegram_chat_id: str = "6421609315"
    notify_2buy: bool = True     # watch-only 2-wallet convergence alerts (noisy)

    # Curation
    curation_interval_hours: float = 6.0
    min_wallet_winrate: float = 0.55
    min_wallet_pnl_sol: float = 5.0
    min_wallet_score: float = 40.0

    # Paths
    wallets_json_path: str = "config/wallets.json"
    db_path: str = "data/smc.db"

    @field_validator("solana_rpc_urls", "solana_ws_urls", "blocked_hours_utc", mode="before")
    @classmethod
    def parse_json_list(cls, v):
        if isinstance(v, str):
            return json.loads(v)
        return v

    model_config = {"env_file": ".env", "env_prefix": "SMC_"}
