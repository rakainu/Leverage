"""Daily refresh of candidate wallets via Hyperliquid leaderboard + manual seed list."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from hlsm.db import Wallet
from hlsm.ingest.hyperliquid_rest import HyperliquidREST

log = logging.getLogger(__name__)


class LeaderboardCrawler:
    def __init__(self, client: HyperliquidREST, *, top_n: int = 100,
                 seed_wallets: list[str] | None = None) -> None:
        self.client = client
        self.top_n = top_n
        self.seed_wallets = [a.strip() for a in (seed_wallets or []) if a and a.strip()]

    def refresh(self, session: Session) -> int:
        """Pull leaderboard (best-effort) + seed wallets; upsert; return count of new addresses."""
        try:
            rows = self.client.leaderboard()
        except Exception:  # noqa: BLE001
            log.warning("HL leaderboard endpoint unavailable; falling back to seed wallets only")
            rows = []

        # Seed wallets always count
        seed_count = self._upsert_seed(session)

        if not rows:
            if seed_count == 0:
                log.warning("leaderboard empty and no seed wallets configured; system has nothing to track")
            return seed_count

        # Sort by 'allTime' window PnL (descending) when present
        def _score_key(r: dict) -> float:
            for w in r.get("windowPerformances") or []:
                if isinstance(w, list) and len(w) >= 2 and w[0] == "allTime":
                    perf = w[1] or {}
                    try:
                        return float(perf.get("pnl") or 0)
                    except (TypeError, ValueError):
                        return 0
            return 0

        rows.sort(key=_score_key, reverse=True)
        top = rows[: self.top_n]
        now = datetime.now(timezone.utc)
        added = seed_count
        for r in top:
            addr = r.get("ethAddress") or r.get("user")
            if not addr:
                continue
            existing = session.get(Wallet, addr)
            if existing is None:
                session.add(Wallet(address=addr, source="leaderboard", discovered_at=now, last_seen_at=now, active=True))
                added += 1
            else:
                existing.last_seen_at = now
                existing.active = True
        session.flush()
        return added

    def _upsert_seed(self, session: Session) -> int:
        """Insert seed wallets if missing. Returns count of newly-added rows."""
        if not self.seed_wallets:
            return 0
        now = datetime.now(timezone.utc)
        added = 0
        for raw in self.seed_wallets:
            addr = raw.lower()
            existing = session.get(Wallet, addr)
            if existing is None:
                session.add(Wallet(address=addr, source="seed", discovered_at=now, last_seen_at=now, active=True))
                added += 1
            else:
                existing.last_seen_at = now
                existing.active = True
        session.flush()
        return added
