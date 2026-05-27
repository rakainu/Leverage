"""Honest port of 'RVWAP Mean Reversion Strategy' (vvedding, TradingView Pine v6).

Rolling-VWAP (volume-weighted, 5-day window) +/- 3sigma fade, NO STOP LOSS:
  long  = close crosses UP through (RVWAP - mult*sigma)   -> exit when high reaches RVWAP (mean)
  short = close crosses DOWN through (RVWAP + mult*sigma)  -> exit when low reaches RVWAP (mean)

Two structural strikes flagged up front: (1) NO hard stop — position bags until the
mean is touched; (2) TP AT the mean (the losing shape from the BB-MR test). This sim
reports the TAIL-RISK truth a normal report hides: worst single-trade loss and the
timeout rate (trades that never reached the mean within the window). Single-position
approximation of pyramiding=3 (conservative — pyramiding would add risk, not remove it).

Honest fills: entry next_open, mean-TP limit on the favorable extreme, no stop,
timeout exit at close after `max_hold` bars. Lighter 0-fee, $7,500 notional.
"""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np
import pandas as pd

from engine import load_symbol
from strategy import kpis
from strat_emavwap import split_oos

NOTIONAL = 7500.0


@dataclass
class RVParams:
    window_bars: int = 1440      # 5 days of 5m bars
    mult: float = 3.0
    min_bars: int = 10
    max_hold: int = 1440         # cap the no-stop bag at the window length
    commission_pct: float = 0.0


def rvwap_bands(df: pd.DataFrame, p: RVParams):
    src = ((df["High"] + df["Low"] + df["Close"]) / 3.0).values
    vol = df["Volume"].values.astype(float)
    w = p.window_bars
    sv = pd.Series(src * vol).rolling(w, min_periods=p.min_bars).sum().values
    v = pd.Series(vol).rolling(w, min_periods=p.min_bars).sum().values
    ssv = pd.Series(vol * src**2).rolling(w, min_periods=p.min_bars).sum().values
    with np.errstate(invalid="ignore", divide="ignore"):
        rv = sv / v
        var = np.maximum(ssv / v - rv**2, 0.0)
        sd = np.sqrt(var)
    return rv, rv + sd * p.mult, rv - sd * p.mult


def run(df: pd.DataFrame, p: RVParams):
    rv, upper, lower = rvwap_bands(df, p)
    o = df["Open"].values; h = df["High"].values; l = df["Low"].values; c = df["Close"].values
    ts = df.index
    n = len(df)
    trades = []
    i = p.window_bars + 1
    while i < n - 1:
        if any(np.isnan(x) for x in (rv[i], lower[i], upper[i], rv[i-1], lower[i-1], upper[i-1])):
            i += 1; continue
        long_sig = c[i] > lower[i] and c[i-1] <= lower[i-1]
        short_sig = c[i] < upper[i] and c[i-1] >= upper[i-1]
        side = "long" if long_sig else ("short" if short_sig else None)
        if side is None:
            i += 1; continue

        entry = float(o[i+1]); base = NOTIONAL / entry
        exit_price = None; reason = None; jend = min(i + 1 + p.max_hold, n)
        for j in range(i + 1, jend):
            tp = rv[j]                          # moving mean target, NO stop
            hit = (h[j] >= tp) if side == "long" else (l[j] <= tp)
            if not np.isnan(tp) and hit:
                exit_price = tp; reason = "mean"; break
        if exit_price is None:
            exit_price = float(c[jend-1]); reason = "timeout"; j = jend - 1
        pnl = (exit_price-entry)*base if side == "long" else (entry-exit_price)*base
        fee = (NOTIONAL + (exit_price/entry)*NOTIONAL) * p.commission_pct
        trades.append({"side": side, "exit_reason": reason, "pnl_net": pnl-fee})
        i = j + 1
    return pd.DataFrame(trades)


if __name__ == "__main__":
    import sys
    symbols = sys.argv[1:] or ["SOL", "BTC", "ETH", "ZEC"]
    print("RVWAP Mean Reversion (NO STOP) — HONEST engine, Lighter 0-fee, $7,500 notional, 180d 5m")
    print(f"{'sym':>4} {'mult':>4} {'n':>5} {'WR':>6} {'net$':>8} {'PF':>5} {'maxDD$':>8} {'avg$':>7} {'OOS$':>8} {'worstLoss$':>10} {'timeout%':>8}")
    for sym in symbols:
        df = load_symbol(sym, "5m", days_back=180)
        for mult in [2.0, 3.0]:
            t = run(df, RVParams(mult=mult))
            if t.empty or len(t) < 15:
                print(f"{sym:>4} {mult:>4.1f}   (<15 trades)"); continue
            k = kpis(t); _, oos = split_oos(t); ok = kpis(oos)
            worst = t["pnl_net"].min()
            to_pct = 100 * (t["exit_reason"] == "timeout").mean()
            print(f"{sym:>4} {mult:>4.1f} {k['n']:>5} {k['win_rate']*100:>5.1f}% {k['net_pnl']:>8,.0f} "
                  f"{k['profit_factor']:>5.2f} {k['max_dd']:>8,.0f} {k['avg_trade']:>7.2f} {ok['net_pnl']:>8,.0f} "
                  f"{worst:>10,.0f} {to_pct:>7.1f}%")
