"""GMGN-specific wallet ranker — scores wallets 0-100 using GMGN data.

Unlike the base WalletScorer (which uses all-time stats), this ranker uses
time-windowed data from GMGN (1d/7d/30d) to prioritize currently-active,
consistently-profitable wallets.

Weights:
  - 7d win rate (25%): consistent recent profitability
  - 7d profit USD (25%): actually making money now
  - Recency (20%): active in last N days
  - 30d consistency (15%): not a one-week fluke
  - Trade volume (10%): enough activity for convergence signals
  - Hit rate on big wins (5%): finds 2x+ trades
"""

import logging
import math
from datetime import datetime, timezone

logger = logging.getLogger("smc.curation.gmgn_ranker")


class GMGNRanker:
    """Scores wallets 0-100 based on GMGN time-windowed performance."""

    WEIGHTS = {
        "winrate_7d": 0.25,
        "profit_7d": 0.25,
        "recency": 0.20,
        "consistency_30d": 0.15,
        "volume": 0.10,
        "big_wins": 0.05,
    }

    def score(self, gmgn_data: dict) -> dict:
        """Return detailed scoring breakdown + composite score.

        Args:
            gmgn_data: single wallet dict from Apify copytrade scraper

        Returns:
            {
                "composite": 0-100,
                "breakdown": {component: score},
                "flags": [str],  # e.g., "dormant", "losing_7d"
            }
        """
        scores = {}
        flags = []

        # ── 7d win rate (25%) ──
        # 50% = 40pts, 65% = 80pts, 75%+ = 100pts
        wr_7d = self._float(gmgn_data.get("winrate_7d", 0))
        if wr_7d < 0.45:
            scores["winrate_7d"] = 0
            flags.append(f"low_winrate_7d_{wr_7d*100:.0f}pct")
        else:
            scores["winrate_7d"] = max(0, min(100, (wr_7d - 0.40) / 0.35 * 100))

        # ── 7d profit USD (25%) ──
        # $500 = 40pts, $5K = 70pts, $50K+ = 100pts (log scale)
        profit_7d = self._float(gmgn_data.get("realized_profit_7d", 0))
        if profit_7d <= 0:
            scores["profit_7d"] = 0
            flags.append("losing_7d")
        else:
            scores["profit_7d"] = min(100, (math.log10(profit_7d + 1) / math.log10(50001)) * 100)

        # ── Recency (20%) ──
        # Active today/yesterday = 100, 3d ago = 60, 7d = 20, >7d = 0
        last_active = gmgn_data.get("last_active", 0)
        if last_active:
            now = datetime.now(timezone.utc).timestamp()
            hours_ago = (now - last_active) / 3600
            if hours_ago < 48:
                scores["recency"] = 100
            elif hours_ago < 72:
                scores["recency"] = 80
            elif hours_ago < 120:
                scores["recency"] = 50
            elif hours_ago < 168:
                scores["recency"] = 20
            else:
                scores["recency"] = 0
                flags.append(f"dormant_{hours_ago/24:.0f}d")
        else:
            scores["recency"] = 0
            flags.append("no_activity_data")

        # ── 30d consistency (15%) ──
        # Strong 30d PnL confirms they're not a 1-week wonder
        profit_30d = self._float(gmgn_data.get("realized_profit_30d", 0))
        wr_30d = self._float(gmgn_data.get("winrate_30d", 0))
        if profit_30d > 0 and wr_30d >= 0.50:
            scores["consistency_30d"] = min(100, (math.log10(profit_30d + 1) / math.log10(100001)) * 100)
        else:
            scores["consistency_30d"] = 0
            if profit_30d <= 0:
                flags.append("losing_30d")

        # ── Volume / activity (10%) ──
        # 7d txs: 10-50 = sweet spot for memecoin traders, too many = bot
        txs_7d = int(gmgn_data.get("txs_7d", 0) or 0)
        if txs_7d < 5:
            scores["volume"] = 0
            flags.append("low_activity")
        elif 10 <= txs_7d <= 200:
            scores["volume"] = 100
        elif txs_7d <= 500:
            scores["volume"] = 70
        elif txs_7d <= 1000:
            scores["volume"] = 40
        else:
            scores["volume"] = 0
            flags.append(f"bot_like_{txs_7d}txs")

        # ── Big wins (5%) ──
        # Count of 2x+ hits in the 7d window
        pnl_2x_5x = int(gmgn_data.get("pnl_2x_5x_num_7d", 0) or 0)
        pnl_gt_5x = int(gmgn_data.get("pnl_gt_5x_num_7d", 0) or 0)
        big_wins = pnl_2x_5x + (pnl_gt_5x * 2)
        if big_wins >= 5:
            scores["big_wins"] = 100
        elif big_wins >= 2:
            scores["big_wins"] = 60
        elif big_wins >= 1:
            scores["big_wins"] = 30
        else:
            scores["big_wins"] = 0

        # Composite
        composite = sum(scores[k] * self.WEIGHTS[k] for k in self.WEIGHTS)

        return {
            "composite": round(composite, 1),
            "breakdown": {k: round(v, 1) for k, v in scores.items()},
            "flags": flags,
        }

    def meets_minimum(self, gmgn_data: dict, min_composite: float = 60.0) -> bool:
        """Hard floor check — wallet must meet all minimums AND score above threshold."""
        # Bot filter
        txs_7d = int(gmgn_data.get("txs_7d", 0) or 0)
        if txs_7d > 1000:
            return False

        # Recency floor — must be active in last 7 days
        last_active = gmgn_data.get("last_active", 0)
        if last_active:
            now = datetime.now(timezone.utc).timestamp()
            if now - last_active > 7 * 86400:
                return False
        else:
            return False

        # Minimum activity
        if txs_7d < 5:
            return False

        # Must be net positive 7d AND 30d
        if self._float(gmgn_data.get("realized_profit_7d", 0)) <= 0:
            return False
        if self._float(gmgn_data.get("realized_profit_30d", 0)) <= 0:
            return False

        # Win rate floor
        if self._float(gmgn_data.get("winrate_7d", 0)) < 0.45:
            return False

        # Score above threshold
        result = self.score(gmgn_data)
        return result["composite"] >= min_composite

    @staticmethod
    def _float(v) -> float:
        try:
            return float(v) if v is not None else 0.0
        except (ValueError, TypeError):
            return 0.0
