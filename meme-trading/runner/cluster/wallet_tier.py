"""Wallet tier enum + in-memory cache loaded from the wallet_tiers DB table."""
from enum import Enum

from runner.db.database import Database


class Tier(Enum):
    A = ("A", 100)
    B = ("B", 60)
    C = ("C", 0)
    U = ("U", 40)

    def __init__(self, label: str, points: int):
        self.label = label
        self.points = points

    @classmethod
    def from_label(cls, label: str) -> "Tier":
        for t in cls:
            if t.label == label:
                return t
        return cls.U


class WalletTierCache:
    """Reads wallet_tiers into an in-memory dict for fast lookups.

    U-tier is the default for wallets with no row (new wallets, pre-bootstrap).
    """

    def __init__(self, db: Database):
        self.db = db
        self._map: dict[str, Tier] = {}

    async def load(self) -> None:
        assert self.db.conn is not None
        new_map: dict[str, Tier] = {}
        async with self.db.conn.execute(
            "SELECT wallet_address, tier FROM wallet_tiers"
        ) as cur:
            async for wallet, tier_label in cur:
                new_map[wallet] = Tier.from_label(tier_label)
        self._map = new_map

    def tier_of(self, wallet_address: str) -> Tier:
        return self._map.get(wallet_address, Tier.U)

    def count_ab(self, wallets: list[str]) -> int:
        return sum(
            1 for w in wallets if self.tier_of(w) in (Tier.A, Tier.B)
        )

    def filter_ab(self, wallets: list[str]) -> list[str]:
        return [w for w in wallets if self.tier_of(w) in (Tier.A, Tier.B)]
