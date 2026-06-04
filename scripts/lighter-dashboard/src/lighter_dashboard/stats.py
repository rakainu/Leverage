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


def unrealized_pnl(side: str, entry: float, mark: float, base: float) -> float:
    if side == "long":
        return (mark - entry) * base
    return (entry - mark) * base
