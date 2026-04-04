"""Wallet scoring algorithm — rates wallets 0-100 based on trading performance."""

import logging
import math
from datetime import datetime, timezone

logger = logging.getLogger("smc.curation.scorer")


class WalletScorer:
    """Scores wallets 0-100 based on trading performance metrics.

    Weights:
      - Win rate (30%): consistent profitability
      - Total PnL (25%): absolute performance
      - Consistency (20%): number of trades (not one-hit wonder)
      - Recency (15%): active recently
      - Hold style (10%): prefer memecoin-style short holds
    """

    WEIGHTS = {
        "win_rate": 0.30,
        "total_pnl": 0.25,
        "consistency": 0.20,
        "recency": 0.15,
        "hold_style": 0.10,
    }

    def score(self, stats: dict) -> float:
        """Return composite score 0-100."""
        scores = {}

        # Win rate: 55% = 50pts, 80%+ = 100pts
        wr = stats.get("win_rate", 0)
        scores["win_rate"] = max(0, min(100, (wr - 0.45) / 0.35 * 100))

        # Total PnL: 5 SOL = 50pts, 50+ SOL = 100pts (log scale)
        pnl = max(0, stats.get("total_pnl_sol", 0))
        if pnl > 0:
            scores["total_pnl"] = min(100, (math.log10(pnl + 1) / math.log10(51)) * 100)
        else:
            scores["total_pnl"] = 0

        # Consistency: 50+ trades = 100pts
        trades = stats.get("total_trades", 0)
        scores["consistency"] = min(100, (trades / 50) * 100)

        # Recency: active in last 7 days = 100, last 30 = 50, older = 0
        last_active = stats.get("last_active")
        if last_active:
            if isinstance(last_active, (int, float)):
                last_dt = datetime.fromtimestamp(last_active, tz=timezone.utc)
            elif isinstance(last_active, str):
                last_dt = datetime.fromisoformat(last_active)
            else:
                last_dt = last_active
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
            days_ago = (datetime.now(timezone.utc) - last_dt).days
            scores["recency"] = max(0, 100 - (days_ago / 30) * 100)
        else:
            scores["recency"] = 0

        # Hold style: prefer avg hold < 120min for memecoin style
        hold = stats.get("avg_hold_minutes", 999)
        scores["hold_style"] = max(0, 100 - (hold / 120) * 50)

        total = sum(scores[k] * self.WEIGHTS[k] for k in self.WEIGHTS)
        return round(total, 1)

    def meets_minimum(self, stats: dict, settings) -> bool:
        """Check if a wallet meets minimum thresholds before scoring."""
        if stats.get("win_rate", 0) < settings.min_wallet_winrate:
            return False
        if stats.get("total_pnl_sol", 0) < settings.min_wallet_pnl_sol:
            return False
        return True
