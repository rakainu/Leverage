"""Wallet vetting funnel — multi-stage gate on every GMGN-discovered candidate.

Goal (per Rich's explicit direction, 2026-04-14): we can't just trust any
memecoin trader GMGN surfaces. This module enforces:

  Stage 2 — GMGN-side hard filters  (cheap, uses scraped Apify data)
  Stage 3 — Helius on-chain verify   (ground truth — do OUR tier math)
  Stage 4 — Behavioral anti-spoof    (concentration, burst-buys, rug-riding)

Stage 5 (shadow period) and Stage 6 (consensus check) are time-gated and
handled by runner.curation.shadow_promoter instead.

Each stage emits a VettingResult with passed / reason / sub_scores. Reasons
are recorded to the gmgn_candidates table so we can audit rejections later.
"""
from __future__ import annotations

import json
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from runner.config.weights_loader import WeightsLoader
from runner.curation.tier_rebuilder import TierRebuilder, _Pair
from runner.db.database import Database
from runner.utils.logging import get_logger

logger = get_logger("runner.curation.wallet_vetting")


@dataclass
class VettingResult:
    passed: bool
    reason: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)


def _f(v: Any) -> float:
    try:
        return float(v) if v is not None else 0.0
    except (ValueError, TypeError):
        return 0.0


def _i(v: Any) -> int:
    try:
        return int(v) if v is not None else 0
    except (ValueError, TypeError):
        return 0


def stage2_gmgn_filters(raw: dict, weights: WeightsLoader) -> VettingResult:
    """Hard filters using the GMGN-scraped data. Cheap, no network calls.

    Rejects: bots, dormant wallets, one-hit-wonders, small-sample winners,
    losers, excessive concentration, freshly-spun sybils.
    """
    cfg = weights.get("gmgn_discovery.gmgn_filters", {}) or {}

    # Win rates
    wr_7d = _f(raw.get("winrate_7d"))
    wr_30d = _f(raw.get("winrate_30d"))
    if wr_7d < _f(cfg.get("min_7d_winrate", 0.55)):
        return VettingResult(False, f"low_winrate_7d_{wr_7d*100:.0f}pct")
    if wr_30d < _f(cfg.get("min_30d_winrate", 0.50)):
        return VettingResult(False, f"low_winrate_30d_{wr_30d*100:.0f}pct")

    # 7d realized profit
    profit_7d = _f(raw.get("realized_profit_7d"))
    if profit_7d < _f(cfg.get("min_7d_pnl_usd", 3000)):
        return VettingResult(False, f"low_profit_7d_${profit_7d:.0f}")

    # Trade count band
    txs_7d = _i(raw.get("txs_7d"))
    txs_30d = _i(raw.get("txs_30d")) or txs_7d * 4  # estimate if missing
    min_tx = _i(cfg.get("min_trade_count_30d", 20))
    max_tx = _i(cfg.get("max_trade_count_30d", 500))
    if txs_30d < min_tx:
        return VettingResult(False, f"low_activity_30d_{txs_30d}")
    if txs_30d > max_tx:
        return VettingResult(False, f"too_active_30d_{txs_30d}_bot_risk")

    # Hold time
    avg_hold = _f(raw.get("avg_hold_min")) or _f(raw.get("avg_hold_minutes"))
    max_hold = _f(cfg.get("max_avg_hold_minutes", 240))
    if avg_hold > max_hold:
        return VettingResult(False, f"long_hold_{avg_hold:.0f}min_not_a_scalper")

    # Largest trade concentration — if one trade >70% of PnL it was a lottery
    largest_pct = _f(raw.get("largest_trade_pct_of_pnl"))
    max_largest = _f(cfg.get("max_largest_trade_pct_of_pnl", 0.70))
    if largest_pct > 0 and largest_pct > max_largest:
        return VettingResult(False, f"lottery_ticket_{largest_pct*100:.0f}pct_single")

    # Realized / unrealized ratio — guard against paper gains
    unrealized = _f(raw.get("unrealized_profit"))
    realized = _f(raw.get("realized_profit_30d")) or _f(raw.get("realized_profit_7d"))
    if realized + unrealized > 0:
        ratio = realized / (realized + unrealized) if (realized + unrealized) else 0.0
        min_ratio = _f(cfg.get("min_realized_to_unrealized_ratio", 0.50))
        if ratio < min_ratio:
            return VettingResult(False, f"mostly_unrealized_ratio_{ratio:.2f}")

    # Wallet age on-chain
    first_seen = _i(raw.get("first_seen_unix")) or _i(raw.get("created_at_unix"))
    require_age_days = _i(cfg.get("require_age_days", 30))
    if first_seen:
        age_days = (time.time() - first_seen) / 86400
        if age_days < require_age_days:
            return VettingResult(False, f"too_young_{age_days:.0f}d")

    # Composite score floor (reuses GMGNRanker's assessment if present)
    composite = _f(raw.get("composite_score"))
    min_composite = _f(cfg.get("min_composite_score", 70.0))
    if composite and composite < min_composite:
        return VettingResult(False, f"low_composite_{composite:.0f}")

    return VettingResult(True, detail={
        "winrate_7d": wr_7d, "winrate_30d": wr_30d,
        "profit_7d": profit_7d, "txs_30d": txs_30d, "composite": composite,
    })


async def stage3_helius_verify(
    wallet: str,
    tier_rebuilder: TierRebuilder,
    weights: WeightsLoader,
) -> VettingResult:
    """Re-tier the wallet from ground-truth Helius trade history. A GMGN
    wallet that doesn't survive our own tier math doesn't get in.
    """
    cfg = weights.get("gmgn_discovery.helius_verify", {}) or {}
    result = await tier_rebuilder.verify_single_wallet(wallet)

    min_trades = _i(cfg.get("min_closed_trades", 15))
    if result["closed_trades"] < min_trades:
        return VettingResult(
            False, f"helius_thin_{result['closed_trades']}_trades",
            detail=result,
        )
    min_wr = _f(cfg.get("min_winrate", 0.45))
    if result["win_rate"] < min_wr:
        return VettingResult(
            False, f"helius_low_winrate_{result['win_rate']*100:.0f}pct",
            detail=result,
        )
    min_pnl = _f(cfg.get("min_pnl_sol", 20))
    if result["pnl_sol"] < min_pnl:
        return VettingResult(
            False, f"helius_low_pnl_{result['pnl_sol']:.1f}sol",
            detail=result,
        )
    required_tier = str(cfg.get("required_tier", "B")).upper()
    # Allow A if required=B; require exact A if required=A
    if required_tier == "A" and result["tier"] != "A":
        return VettingResult(
            False, f"helius_tier_{result['tier']}_below_A",
            detail=result,
        )
    if required_tier == "B" and result["tier"] not in ("A", "B"):
        return VettingResult(
            False, f"helius_tier_{result['tier']}_below_B",
            detail=result,
        )
    return VettingResult(True, detail=result)


def stage4_behavioral(
    pairs: list[_Pair],
    weights: WeightsLoader,
) -> VettingResult:
    """Behavioral anti-spoof checks using the trade pairs from Stage 3.

    - Rejects copy-bots (>N% of trades on a single token).
    - Rejects burst-buyers (>M% of trades <60s apart).
    - Rejects one-pony shows (top-3 tokens = most of PnL).
    """
    cfg = weights.get("gmgn_discovery.behavioral", {}) or {}
    if not pairs:
        return VettingResult(False, "no_pairs_for_behavioral")

    n = len(pairs)

    # Single-token concentration
    by_mint: dict[str, int] = defaultdict(int)
    for p in pairs:
        by_mint[p.mint] += 1
    max_single_token = max(by_mint.values())
    single_pct = max_single_token / n
    max_single_pct = _f(cfg.get("max_single_token_pct", 0.80))
    if single_pct > max_single_pct:
        return VettingResult(
            False, f"single_token_concentration_{single_pct*100:.0f}pct",
            detail={"top_mint_count": max_single_token, "total": n},
        )

    # High-frequency burst: fraction of consecutive buys <60s apart
    buy_times = sorted(p.entry_time for p in pairs)
    bursts = sum(
        1 for a, b in zip(buy_times, buy_times[1:])
        if (b - a).total_seconds() < 60
    )
    burst_pct = bursts / max(n - 1, 1)
    max_burst = _f(cfg.get("max_hf_burst_pct", 0.50))
    if burst_pct > max_burst:
        return VettingResult(
            False, f"hf_burst_{burst_pct*100:.0f}pct",
            detail={"bursts": bursts, "total_intervals": n - 1},
        )

    # Top-3 PnL concentration
    by_mint_pnl: dict[str, float] = defaultdict(float)
    for p in pairs:
        by_mint_pnl[p.mint] += p.pnl_sol
    total_pnl = sum(by_mint_pnl.values())
    top3 = sorted(by_mint_pnl.values(), reverse=True)[:3]
    top3_pnl = sum(top3)
    if total_pnl > 0:
        top3_pct = top3_pnl / total_pnl
        max_top3 = _f(cfg.get("max_top3_pnl_pct", 0.90))
        if top3_pct > max_top3:
            return VettingResult(
                False, f"top3_pnl_concentration_{top3_pct*100:.0f}pct",
                detail={"top3_sol": top3_pnl, "total_sol": total_pnl},
            )

    return VettingResult(True, detail={
        "single_token_pct": single_pct,
        "burst_pct": burst_pct,
    })


class WalletVetter:
    """Runs Stages 2→3→4 on a candidate and persists stage/reason.

    Candidates start at stage='raw' after Apify discovery. This class advances
    them to 'stage2_passed' → 'stage3_passed' → 'stage4_passed' → 'shadow',
    or marks them 'rejected' with a reason.
    """

    def __init__(
        self,
        db: Database,
        tier_rebuilder: TierRebuilder,
        weights: WeightsLoader,
    ):
        self.db = db
        self.tier_rebuilder = tier_rebuilder
        self.weights = weights

    async def vet_candidate(self, wallet: str) -> str:
        """Vet one candidate. Returns the final stage it landed at.
        Caller is expected to have already inserted the raw row."""
        assert self.db.conn is not None
        async with self.db.conn.execute(
            "SELECT raw_json, composite_score FROM gmgn_candidates "
            "WHERE wallet_address = ? AND stage = 'raw'",
            (wallet,),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            logger.debug("vet_skip_not_raw", wallet=wallet)
            return "skipped"
        raw = json.loads(row[0])
        raw["composite_score"] = row[1]

        # Stage 2
        r2 = stage2_gmgn_filters(raw, self.weights)
        if not r2.passed:
            await self._mark(wallet, "rejected", f"stage2:{r2.reason}")
            return "rejected"
        await self._mark(wallet, "stage2_passed", None)

        # Stage 3
        r3 = await stage3_helius_verify(wallet, self.tier_rebuilder, self.weights)
        if not r3.passed:
            await self._mark(
                wallet, "rejected", f"stage3:{r3.reason}",
                helius_closed_trades=r3.detail.get("closed_trades", 0),
                helius_win_rate=r3.detail.get("win_rate", 0.0),
                helius_pnl_sol=r3.detail.get("pnl_sol", 0.0),
                helius_tier=r3.detail.get("tier"),
            )
            return "rejected"
        await self._mark(
            wallet, "stage3_passed", None,
            helius_verified=1,
            helius_closed_trades=r3.detail["closed_trades"],
            helius_win_rate=r3.detail["win_rate"],
            helius_pnl_sol=r3.detail["pnl_sol"],
            helius_tier=r3.detail["tier"],
        )

        # Stage 4
        r4 = stage4_behavioral(r3.detail.get("pairs", []), self.weights)
        if not r4.passed:
            await self._mark(
                wallet, "rejected", f"stage4:{r4.reason}",
                behavioral_pass=0,
                behavioral_reason=r4.reason,
            )
            return "rejected"
        await self._mark(
            wallet, "stage4_passed", None,
            behavioral_pass=1,
            behavioral_reason=None,
        )

        # Stage 5: promote to shadow. Writes the shadow tier entry so the
        # wallet monitor starts observing its buys; the shadow_promoter loop
        # is what eventually graduates it out of shadow.
        await self._mark(wallet, "shadow", None)
        await self._promote_to_shadow_tier(wallet, r3.detail["tier"], r3.detail["win_rate"])
        logger.info(
            "candidate_entered_shadow",
            wallet=wallet,
            helius_tier=r3.detail["tier"],
            helius_wr=round(r3.detail["win_rate"], 2),
        )
        return "shadow"

    async def _mark(self, wallet: str, stage: str, reason: str | None, **fields):
        assert self.db.conn is not None
        sets = ["stage = ?", "updated_at = CURRENT_TIMESTAMP"]
        params: list[Any] = [stage]
        if reason is not None:
            sets.append("stage_reason = ?")
            params.append(reason)
        if stage == "shadow":
            sets.append("shadow_started_at = CURRENT_TIMESTAMP")
        if stage == "rejected":
            sets.append("rejected_at = CURRENT_TIMESTAMP")
        for k, v in fields.items():
            sets.append(f"{k} = ?")
            params.append(v)
        params.append(wallet)
        await self.db.conn.execute(
            f"UPDATE gmgn_candidates SET {', '.join(sets)} WHERE wallet_address = ?",
            params,
        )
        await self.db.conn.commit()

    async def _promote_to_shadow_tier(self, wallet: str, helius_tier: str, wr: float) -> None:
        """Write an 'S' (shadow) row into wallet_tiers so the wallet monitor
        starts tracking it. S tier is not counted in convergence clusters."""
        assert self.db.conn is not None
        await self.db.conn.execute(
            """INSERT INTO wallet_tiers
               (wallet_address, tier, win_rate, trade_count, pnl_sol,
                source, source_stage, updated_at)
               VALUES (?, 'S', ?, 0, 0, 'gmgn-live', 'shadow', CURRENT_TIMESTAMP)
               ON CONFLICT(wallet_address) DO UPDATE SET
                 tier = CASE
                   WHEN wallet_tiers.tier IN ('A','B') THEN wallet_tiers.tier
                   ELSE 'S' END,
                 source = COALESCE(wallet_tiers.source, 'gmgn-live'),
                 source_stage = CASE
                   WHEN wallet_tiers.tier IN ('A','B') THEN wallet_tiers.source_stage
                   ELSE 'shadow' END,
                 updated_at = CURRENT_TIMESTAMP""",
            (wallet, wr),
        )
        await self.db.conn.commit()
