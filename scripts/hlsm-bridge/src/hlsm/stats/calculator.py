"""Compute risk-adjusted statistics for a wallet's trading history.

Primary input is the per-fill `closedPnl` stream (HL's authoritative realized PnL
per fill). Each fill with closedPnl != 0 is a realized trade. We avoid the
position-reconstruction approach because active scalpers scale in/out without
ever flattening, and the reconstructor would emit zero closed trades for them.

Metrics:
- sharpe_proxy: per-trade mean / per-trade stdev (PnL-USD scale, then sqrt(N))
- max_dd_pct: peak-to-trough drawdown of cumulative PnL, percent of peak
- win_rate: fraction of realized trades with closedPnl > 0
- sample_size: count of realized trades
- avg_hold_seconds: NOT directly available from fills; reported from hl_positions
  when those exist, else 0 (the scoring layer doesn't use it as a hard gate)
- max_single_trade_pnl_share: |largest single trade PnL| / sum |pnl|
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from hlsm.db import Fill, HlPosition


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
    """Compute stats primarily from per-fill closedPnl (the HL-authoritative number).

    Falls back to hl_positions for avg_hold_seconds only, since fills don't carry
    open-time info.
    """
    fills = session.execute(
        select(Fill.ts, Fill.closed_pnl, Fill.px, Fill.sz)
        .where(Fill.wallet_address == wallet_address)
        .order_by(Fill.ts)
    ).all()

    # Realized trades = fills with non-null, non-zero closedPnl
    pnl_values: list[float] = []
    pnl_pct_values: list[float] = []
    last_ts: datetime | None = None
    for ts, closed_pnl, px, sz in fills:
        if closed_pnl is None:
            continue
        pnl = float(closed_pnl)
        if pnl == 0:
            continue
        pnl_values.append(pnl)
        notional = float(px) * float(sz) if px and sz else 0.0
        pct = (pnl / notional) * 100.0 if notional > 0 else 0.0
        pnl_pct_values.append(pct)
        last_ts = ts

    if not pnl_values:
        return WalletStats()

    # avg_hold_seconds from hl_positions when available (best-effort)
    pos_rows = session.execute(
        select(HlPosition.hold_seconds).where(
            HlPosition.wallet_address == wallet_address,
            HlPosition.status == "closed",
            HlPosition.hold_seconds.is_not(None),
        )
    ).all()
    holds = [int(h[0]) for h in pos_rows if h[0] is not None]
    avg_hold = int(sum(holds) / len(holds)) if holds else 0

    n = len(pnl_values)
    wins = sum(1 for p in pnl_values if p > 0)
    win_rate = wins / n
    last_at = last_ts

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
            peak_for_dd = peak
    # DD denominator floored at $1 to avoid blowups when peak equity is tiny
    denom = max(peak_for_dd, 1.0)
    max_dd_pct = min(9999.0, (max_dd_abs / denom) * 100.0) if max_dd_abs > 0 else 0.0

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
