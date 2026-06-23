"""Task 9 — monthly P&L distribution + summary for a portfolio equity curve."""
from __future__ import annotations
import numpy as np
import pandas as pd


def monthly_pnl(eq: pd.Series) -> pd.DataFrame:
    m = eq.resample("ME").last().dropna()
    r = m.pct_change().dropna() * 100
    return pd.DataFrame({"return_pct": r})


def summary(eq: pd.Series, trades) -> dict:
    mp = monthly_pnl(eq)["return_pct"]
    peak = eq.cummax()
    dd = ((peak - eq) / peak).max() * 100 if len(eq) else 0.0
    return dict(
        total_return_pct=(eq.iloc[-1] / eq.iloc[0] - 1) * 100 if len(eq) else 0.0,
        max_dd=float(dd),
        n_months=int(len(mp)),
        pct_months_green=float((mp > 0).mean() * 100) if len(mp) else 0.0,
        avg_month=float(mp.mean()) if len(mp) else 0.0,
        worst_month=float(mp.min()) if len(mp) else 0.0,
        best_month=float(mp.max()) if len(mp) else 0.0,
        n_trades=len(trades),
    )
