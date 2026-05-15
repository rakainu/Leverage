"""Composite 0-100 wallet scorer.

Anti-fluke filters fire BEFORE scoring. A wallet that fails any filter is forced to
score 0 with `passes_anti_fluke=False` and a reason. Filters:
- min_trades: must have at least N closed trades
- min_days_active: first-to-last-trade span must be at least D days
- max_single_trade_pnl_pct: no single trade may dominate (default 50% of total |PnL|)

Composite is a weighted average across normalized components in [0, 1]:
- sharpe_proxy: tanh(sharpe / 2) clipped to [0, 1]
- max_dd: 1 - clip(max_dd_pct / 50, 0, 1)  (lower DD => higher score)
- win_rate: as-is (already in [0, 1])
- sample_size: log scaled, capped at 1.0 for 500+ trades
- recency: exponential decay using a half-life
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy.orm import Session

from hlsm.db import ScoreHistory, Wallet
from hlsm.stats import WalletStats, compute_wallet_stats


@dataclass
class ScoringConfig:
    min_trades: int = 50
    min_days_active: int = 30
    max_single_trade_pnl_pct: float = 50.0
    recency_half_life_days: int = 30
    weights: dict = None

    def __post_init__(self) -> None:
        if self.weights is None:
            self.weights = {
                "sharpe": 0.30,
                "max_dd": 0.20,
                "win_rate": 0.20,
                "sample_size": 0.15,
                "recency": 0.15,
            }


@dataclass
class ScoredWallet:
    address: str
    composite: float          # 0..100
    sharpe_proxy: float
    max_dd_pct: float
    win_rate: float
    sample_size: int
    avg_hold_seconds: int
    recency_weight: float
    passes_anti_fluke: bool
    fluke_reason: str | None = None


def _normalize_sharpe(s: float) -> float:
    return max(0.0, min(1.0, math.tanh(s / 2.0)))


def _normalize_max_dd(dd_pct: float) -> float:
    return max(0.0, 1.0 - min(dd_pct, 50.0) / 50.0)


def _normalize_sample_size(n: int) -> float:
    if n <= 0:
        return 0.0
    return min(1.0, math.log(n + 1) / math.log(501))


def _recency_weight(last_at: datetime | None, *, half_life_days: int) -> float:
    if last_at is None:
        return 0.0
    now = datetime.now(timezone.utc)
    if last_at.tzinfo is None:
        last_at = last_at.replace(tzinfo=timezone.utc)
    age_days = max(0.0, (now - last_at).total_seconds() / 86400.0)
    decay = math.exp(-age_days * math.log(2) / max(half_life_days, 1))
    return max(0.0, min(1.0, decay))


def score_wallet(stats: WalletStats, *, config: ScoringConfig) -> ScoredWallet:
    """Pure scoring from a stats snapshot. No DB writes."""
    fluke_reason: str | None = None
    passes = True
    if stats.sample_size < config.min_trades:
        passes = False
        fluke_reason = f"sample_size {stats.sample_size} < {config.min_trades}"
    elif stats.max_single_trade_pnl_share * 100 > config.max_single_trade_pnl_pct:
        passes = False
        fluke_reason = f"single trade dominates ({stats.max_single_trade_pnl_share:.0%} of total |PnL|)"

    if not passes:
        return ScoredWallet(
            address="",  # filled by caller
            composite=0.0,
            sharpe_proxy=max(-100.0, min(100.0, stats.sharpe_proxy)),
            max_dd_pct=max(0.0, min(9999.0, stats.max_dd_pct)),
            win_rate=max(0.0, min(1.0, stats.win_rate)),
            sample_size=stats.sample_size,
            avg_hold_seconds=stats.avg_hold_seconds,
            recency_weight=0.0,
            passes_anti_fluke=False,
            fluke_reason=fluke_reason,
        )

    # Clamp components defensively so DB Numeric columns never overflow
    clamped_sharpe = max(-100.0, min(100.0, stats.sharpe_proxy))
    clamped_dd = max(0.0, min(9999.0, stats.max_dd_pct))
    sharpe_n = _normalize_sharpe(clamped_sharpe)
    dd_n = _normalize_max_dd(clamped_dd)
    win_n = max(0.0, min(1.0, stats.win_rate))
    sample_n = _normalize_sample_size(stats.sample_size)
    rec_n = _recency_weight(stats.last_trade_at, half_life_days=config.recency_half_life_days)

    w = config.weights
    composite_01 = (
        sharpe_n * w["sharpe"]
        + dd_n * w["max_dd"]
        + win_n * w["win_rate"]
        + sample_n * w["sample_size"]
        + rec_n * w["recency"]
    )
    composite = round(composite_01 * 100.0, 2)
    return ScoredWallet(
        address="",
        composite=composite,
        sharpe_proxy=clamped_sharpe,
        max_dd_pct=clamped_dd,
        win_rate=max(0.0, min(1.0, stats.win_rate)),
        sample_size=stats.sample_size,
        avg_hold_seconds=stats.avg_hold_seconds,
        recency_weight=rec_n,
        passes_anti_fluke=True,
        fluke_reason=None,
    )


def score_all(session: Session, *, config: ScoringConfig, addresses: list[str] | None = None) -> list[ScoredWallet]:
    """Recompute scores for every active wallet (or a subset). Persists to scores_history."""
    from sqlalchemy import select
    q = select(Wallet).where(Wallet.active.is_(True))
    if addresses:
        q = q.where(Wallet.address.in_(addresses))
    wallets = session.execute(q).scalars().all()

    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    out: list[ScoredWallet] = []
    for w in wallets:
        stats = compute_wallet_stats(session, w.address)
        sw = score_wallet(stats, config=config)
        sw.address = w.address
        # Upsert score row for today
        existing = session.execute(
            select(ScoreHistory).where(
                ScoreHistory.wallet_address == w.address,
                ScoreHistory.snapshot_date == today,
            )
        ).scalar_one_or_none()
        if existing is None:
            existing = ScoreHistory(
                wallet_address=w.address,
                snapshot_date=today,
                composite=Decimal(str(sw.composite)),
                sharpe_proxy=Decimal(str(sw.sharpe_proxy)),
                max_dd_pct=Decimal(str(sw.max_dd_pct)),
                win_rate=Decimal(str(sw.win_rate)),
                sample_size=sw.sample_size,
                avg_hold_seconds=sw.avg_hold_seconds,
                recency_weight=Decimal(str(sw.recency_weight)),
                passes_anti_fluke=sw.passes_anti_fluke,
                fluke_reason=sw.fluke_reason,
            )
            session.add(existing)
        else:
            existing.composite = Decimal(str(sw.composite))
            existing.sharpe_proxy = Decimal(str(sw.sharpe_proxy))
            existing.max_dd_pct = Decimal(str(sw.max_dd_pct))
            existing.win_rate = Decimal(str(sw.win_rate))
            existing.sample_size = sw.sample_size
            existing.avg_hold_seconds = sw.avg_hold_seconds
            existing.recency_weight = Decimal(str(sw.recency_weight))
            existing.passes_anti_fluke = sw.passes_anti_fluke
            existing.fluke_reason = sw.fluke_reason

        # Bump wallet.current_score + trade_count
        w.current_score = Decimal(str(sw.composite))
        w.trade_count = sw.sample_size
        if sw.avg_hold_seconds < 600:
            w.style = "scalper"
        elif sw.avg_hold_seconds < 14400:
            w.style = "swing"
        else:
            w.style = "positional"
        out.append(sw)
    session.flush()
    return out
