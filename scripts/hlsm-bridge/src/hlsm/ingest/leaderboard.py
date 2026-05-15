"""Daily refresh of candidate wallets via Hyperliquid leaderboard."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from hlsm.db import Wallet
from hlsm.ingest.hyperliquid_rest import HyperliquidREST

log = logging.getLogger(__name__)


class LeaderboardCrawler:
    def __init__(self, client: HyperliquidREST, *, top_n: int = 100) -> None:
        self.client = client
        self.top_n = top_n

    def refresh(self, session: Session) -> int:
        """Pull leaderboard, upsert wallets, return count of new addresses added."""
        rows = self.client.leaderboard()
        if not rows:
            log.warning("leaderboard call returned empty")
            return 0

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
        added = 0
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
