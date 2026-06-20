"""Pure metric functions. No DB, no network — fully unit-testable."""
from __future__ import annotations

from typing import Optional


def win_rate(pnls: list[float]) -> float:
    closed = [p for p in pnls if p is not None]
    if not closed:
        return 0.0
    wins = sum(1 for p in closed if p > 0)
    return wins / len(closed)


def profit_factor(pnls: list[float]) -> Optional[float]:
    """Gross win / gross loss. None when there are no losses (undefined)."""
    gross_win = sum(p for p in pnls if p and p > 0)
    gross_loss = -sum(p for p in pnls if p and p < 0)
    if gross_loss == 0:
        return None
    return gross_win / gross_loss


def breakeven_win_rate(avg_win: float, avg_loss: float) -> Optional[float]:
    """Win rate needed to break even given average win/loss sizes.

    breakeven = |avg_loss| / (avg_win + |avg_loss|). Accepts avg_loss as a
    negative value or a positive magnitude. Returns None when there are no wins
    or no losses (the ratio is undefined). Compare a coin's ACTUAL win rate to
    this: actual > breakeven = positive edge; below = bleeding.
    """
    win = abs(avg_win)
    loss = abs(avg_loss)
    if win == 0 or loss == 0:
        return None
    return loss / (win + loss)


def max_drawdown(equity_series: list[float]) -> float:
    """Largest peak-to-trough drop in the series. Returns <= 0.0."""
    peak = float("-inf")
    mdd = 0.0
    for v in equity_series:
        peak = max(peak, v)
        mdd = min(mdd, v - peak)
    return mdd


def max_consecutive_losses(pnls_ordered: list[float]) -> int:
    """Longest run of consecutive losing trades in a chronologically-ordered list."""
    worst = run = 0
    for p in pnls_ordered:
        if p is not None and p <= 0:
            run += 1
            worst = max(worst, run)
        else:
            run = 0
    return worst


def recent_streak(pnls_ordered: list[float], k: int = 3) -> str:
    """Last k outcomes as 'W'/'L', oldest->newest — a cooldown-proximity readout
    (the bridge pauses after 3 straight losses)."""
    last = pnls_ordered[-k:] if pnls_ordered else []
    return " ".join("W" if (p is not None and p > 0) else "L" for p in last)


def unrealized_pnl(side: str, entry: float, mark: float, base: float) -> float:
    if side == "long":
        return (mark - entry) * base
    return (entry - mark) * base
