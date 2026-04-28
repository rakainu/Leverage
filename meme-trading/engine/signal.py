"""Data classes for the SMC trading system."""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class BuyEvent:
    """Represents a single wallet buying a token."""
    wallet_address: str
    token_mint: str
    token_symbol: str | None
    amount_sol: float
    amount_tokens: float
    signature: str
    timestamp: datetime
    dex: str  # "jupiter" | "raydium" | "pump_fun" | "orca" | "unknown"


@dataclass
class ConvergenceSignal:
    """Emitted when N+ distinct wallets buy the same token within the window."""
    token_mint: str
    token_symbol: str | None
    wallets: list[str]
    buy_events: list[BuyEvent]
    first_buy_at: datetime
    signal_at: datetime
    avg_amount_sol: float
    total_amount_sol: float
    convergence_minutes: float = 0.0
    db_id: int | None = None  # set by ConvergenceEngine._persist_signal so executors can FK back
