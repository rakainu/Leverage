"""Compute risk-adjusted statistics for a wallet's reconstructed positions.

We compute these per-wallet metrics:
- sharpe_proxy: per-trade mean return / per-trade stdev * sqrt(N)
- max_dd_pct: peak-to-trough drawdown of the equity curve, expressed in percent
- win_rate: fraction of closed positions with realized_pnl > 0
- sample_size: count of closed positions
- avg_hold_seconds: mean of (closed_at - opened_at)
- max_single_trade_pnl_share: |largest single trade PnL| / |sum of |pnl||
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from hlsm.db import HlPosition


@dataclass
class WalletStats:
    sample_size: int = 0
    sharpe_proxy: float = 0.0
    max_dd_pct: float = 0.0
    win_rate: float = 0.0
    avg_hold_seconds: int = 0
    last_trade_at: datetime | None = None
    max_single_trade_pnl_share: float = 0.0


def compute_wallet_stats(session: Session, wallet_address: str) -> WalletStats:
    rows = session.execute(
        select(HlPosition).where(HlPosition.wallet_address == wallet_address,
                                  HlPosition.status == "closed").order_by(HlPosition.closed_at)
    ).scalars().all()

    if not rows:
        return WalletStats()

    pnl_values: list[float] = [float(r.realized_pnl or 0) for r in rows]
    pnl_pct_values: list[float] = [float(r.realized_pnl_pct or 0) for r in rows]
    holds: list[int] = [int(r.hold_seconds or 0) for r in rows]

    n = len(pnl_values)
    wins = sum(1 for p in pnl_values if p > 0)
    win_rate = wins / n if n else 0.0
    avg_hold = int(sum(holds) / n) if n else 0
    last_at = rows[-1].closed_at

    # Sharpe proxy from per-trade % returns
    mean = sum(pnl_pct_values) / n
    var = sum((p - mean) ** 2 for p in pnl_pct_values) / n if n > 1 else 0.0
    stdev = math.sqrt(var) if var > 0 else 0.0
    sharpe = (mean / stdev) * math.sqrt(n) if stdev > 0 else 0.0

    # Equity curve in absolute USDT; max drawdown
    equity = 0.0
    peak = 0.0
    max_dd_abs = 0.0
    peak_for_dd = 0.0
    for p in pnl_values:
        equity += p
        if equity > peak:
            peak = equity
        dd = peak - equity
        if dd > max_dd_abs:
            max_dd_abs = dd
            peak_for_dd = peak if peak > 0 else max(peak, 1)
    max_dd_pct = (max_dd_abs / peak_for_dd) * 100.0 if peak_for_dd > 0 else 0.0

    total_abs = sum(abs(p) for p in pnl_values)
    max_share = (max(abs(p) for p in pnl_values) / total_abs) if total_abs > 0 else 0.0

    return WalletStats(
        sample_size=n,
        sharpe_proxy=sharpe,
        max_dd_pct=max_dd_pct,
        win_rate=win_rate,
        avg_hold_seconds=avg_hold,
        last_trade_at=last_at,
        max_single_trade_pnl_share=max_share,
    )
