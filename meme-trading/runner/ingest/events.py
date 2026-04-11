"""Ingest event dataclasses."""
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class BuyEvent:
    """A single wallet buying a token — the atomic unit of ingest."""

    signature: str
    wallet_address: str
    token_mint: str
    sol_amount: float
    token_amount: float
    price_sol: float
    block_time: datetime

    def to_db_row(self) -> tuple:
        """Return a tuple matching the buy_events insert column order."""
        return (
            self.signature,
            self.wallet_address,
            self.token_mint,
            self.sol_amount,
            self.token_amount,
            self.price_sol,
            self.block_time.isoformat(),
        )
