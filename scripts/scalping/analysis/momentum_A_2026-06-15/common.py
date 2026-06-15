"""Shared loaders + honest portfolio scorer for the momentum hunt."""
from __future__ import annotations
import os, sys, glob
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "sol_strategy_2026-05-30")))
import btengine as bt  # noqa: E402

DATA = os.path.join(os.path.dirname(__file__), "data")
TF_MIN = 15
LIGHTER = bt.Costs(taker_pct=0.0, maker_pct=0.0, slippage_pct=0.05, funding_pct_per_8h=0.01)
LIGHTER_2X = bt.Costs(taker_pct=0.0, maker_pct=0.0, slippage_pct=0.10, funding_pct_per_8h=0.01)
BLOFIN = bt.Costs()
RISK = bt.RiskCfg(starting_equity=1000.0, risk_frac=0.01, max_leverage=10, liq_buffer=2.5, compounding=True)


def available_coins():
    return sorted(os.path.basename(p).split("_")[1] for p in glob.glob(os.path.join(DATA, "okx_*_15m.parquet")))


def load(coin: str) -> pd.DataFrame:
    return pd.read_parquet(os.path.join(DATA, f"okx_{coin}_15m.parquet")).astype(float)


def portfolio(per_coin_trades: dict, start=1000.0, rf=0.01, compounding=True):
    """Merge coins' trades by exit time onto one shared (compounding) equity."""
    recs = []
    coin_net = {}
    for c, trades in per_coin_trades.items():
        cn = 0.0
        for t in trades:
            recs.append((t.exit_time, t.r_multiple))
            cn += t.r_multiple
        coin_net[c] = cn
    recs.sort(key=lambda x: x[0])
    if not recs:
        return None
    eq = start; curve = [start]; pnls = []
    for _, r in recs:
        base = eq if compounding else start
        pnl = r * rf * base; eq += pnl; pnls.append(pnl); curve.append(eq)
    pnls = np.array(pnls); curve = np.array(curve)
    wins = pnls[pnls > 0]; losses = pnls[pnls < 0]
    pf = wins.sum() / -losses.sum() if losses.sum() < 0 else float("inf")
    peak = np.maximum.accumulate(curve); dd = ((peak - curve) / peak).max() * 100
    rs = np.array([r for _, r in recs])
    t_stat = rs.mean() / (rs.std(ddof=1) / np.sqrt(len(rs))) if len(rs) > 1 else 0.0
    coins_pos = sum(1 for v in coin_net.values() if v > 0)
    return dict(n=len(pnls), pf=pf, wr=(pnls > 0).mean() * 100, avg_r=rs.mean(),
                net_pct=(eq / start - 1) * 100, final=eq, max_dd=dd, t=t_stat,
                coins_pos=coins_pos, ncoins=len(coin_net))


def run_family(fn, dfs: dict, params: dict, costs=LIGHTER, risk=None):
    risk = risk or RISK
    per = {}
    for c, df in dfs.items():
        per[c] = bt.simulate(df, fn(df, **params), costs, risk, TF_MIN)
    return per


def weeks_span(dfs: dict) -> float:
    lo = min(d.index.min() for d in dfs.values())
    hi = max(d.index.max() for d in dfs.values())
    return (hi - lo).days / 7.0
