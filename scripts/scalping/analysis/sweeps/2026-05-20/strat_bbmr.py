"""Honest port of 'Bollinger Mean Reversion' (TradingView Pine v6).

BB fade with an ADX range-regime filter + wick-rejection confirmation:
  long  = low <= lowerBB and bullReject (long lower wick) and ADX <= adxMax
  short = high >= upperBB and bearReject and ADX <= adxMax
  exit  = fixed ATR stop (beyond the trigger bar) / TP at the MOVING basis (mean),
          time stop after N bars. Single position. (EOD-flatten dropped — crypto 24h.)

Honest fills: entry at next_open, TP-at-mean fills on the favorable extreme reaching
the current basis, stop on the adverse extreme + slippage, straddled bar = stop
(conservative), time stop closes at bar close. Lighter 0-fee, $7,500 notional.
"""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np
import pandas as pd

from engine import load_symbol, calc_atr, calc_smma
from strategy import kpis
from strat_emavwap import split_oos

NOTIONAL = 7500.0
SL_SLIP_PCT = 0.0006


def adx(df: pd.DataFrame, length: int) -> np.ndarray:
    h = df["High"].values; l = df["Low"].values; c = df["Close"].values
    n = len(df)
    dm_p = np.zeros(n); dm_m = np.zeros(n); tr = np.zeros(n)
    for i in range(1, n):
        up = h[i] - h[i-1]; dn = l[i-1] - l[i]
        dm_p[i] = up if (up > dn and up > 0) else 0.0
        dm_m[i] = dn if (dn > up and dn > 0) else 0.0
        tr[i] = max(h[i]-l[i], abs(h[i]-c[i-1]), abs(l[i]-c[i-1]))
    idx = df.index
    sp = calc_smma(pd.Series(dm_p, index=idx), length).values
    sm = calc_smma(pd.Series(dm_m, index=idx), length).values
    st = calc_smma(pd.Series(tr, index=idx), length).values
    dx = np.zeros(n)
    for i in range(n):
        if st[i] and not np.isnan(st[i]):
            dip = 100*sp[i]/st[i]; dim = 100*sm[i]/st[i]
            if dip+dim != 0: dx[i] = 100*abs(dip-dim)/(dip+dim)
    return calc_smma(pd.Series(dx, index=idx), length).values


@dataclass
class BBParams:
    bb_len: int = 20
    bb_mult: float = 2.0
    adx_len: int = 14
    adx_max: float = 25
    atr_len: int = 14
    stop_atr: float = 1.0
    time_stop: int = 15
    use_reject: bool = True
    commission_pct: float = 0.0


def run(df: pd.DataFrame, p: BBParams):
    c = df["Close"]
    basis = c.rolling(p.bb_len).mean().values
    dev = (p.bb_mult * c.rolling(p.bb_len).std(ddof=0)).values   # Pine BB = population
    upper = basis + dev; lower = basis - dev
    adxv = adx(df, p.adx_len)
    atr = calc_atr(df, p.atr_len).values
    o = df["Open"].values; h = df["High"].values; l = df["Low"].values; cl = c.values
    op = df["Open"].values
    ts = df.index
    n = len(df)

    trades = []
    i = max(p.bb_len, p.adx_len, p.atr_len) + 1
    while i < n - 1:
        if any(np.isnan(x) for x in (basis[i], adxv[i], atr[i])):
            i += 1; continue
        body = abs(cl[i]-o[i])
        lw = min(cl[i], o[i]) - l[i]; uw = h[i] - max(cl[i], o[i])
        bull_rej = (lw > 1.5*body and cl[i] > o[i]) if p.use_reject else True
        bear_rej = (uw > 1.5*body and cl[i] < o[i]) if p.use_reject else True
        range_ok = adxv[i] <= p.adx_max
        side = None
        if range_ok:
            if l[i] <= lower[i] and bull_rej: side = "long"
            elif h[i] >= upper[i] and bear_rej: side = "short"
        if side is None:
            i += 1; continue

        entry = float(op[i+1]); a = float(atr[i])
        stop = (l[i] - p.stop_atr*a) if side == "long" else (h[i] + p.stop_atr*a)
        base = NOTIONAL / entry
        exit_price = None; reason = None; jend = min(i + 1 + 288, n)
        for j in range(i + 1, jend):
            hit_sl = (l[j] <= stop) if side == "long" else (h[j] >= stop)
            # TP at the MOVING mean (basis updates each bar, like Pine limit=basis)
            tp = basis[j]
            hit_tp = (h[j] >= tp) if side == "long" else (l[j] <= tp)
            if hit_sl:
                slip = entry*SL_SLIP_PCT
                exit_price = (stop - slip) if side == "long" else (stop + slip)
                reason = "sl"; break
            if hit_tp:
                exit_price = tp; reason = "tp"; break
            if (j - i) >= p.time_stop:
                exit_price = float(cl[j]); reason = "time"; break
        if exit_price is None:
            exit_price = float(cl[jend-1]); reason = "timeout"; j = jend - 1
        pnl = (exit_price-entry)*base if side == "long" else (entry-exit_price)*base
        fee = (NOTIONAL + (exit_price/entry)*NOTIONAL) * p.commission_pct
        trades.append({"side": side, "entry_ts": ts[i+1], "exit_reason": reason, "pnl_net": pnl-fee})
        i = j + 1
    return pd.DataFrame(trades)


if __name__ == "__main__":
    import sys
    symbols = sys.argv[1:] or ["SOL", "BTC", "ETH", "ZEC"]
    print("Bollinger Mean Reversion — HONEST engine, Lighter 0-fee, $7,500 notional, 180d 5m")
    grid = []
    for mult in [1.5, 2.0, 2.5]:
        for amax in [20, 25, 30]:
            grid.append(BBParams(bb_mult=mult, adx_max=amax))
    for sym in symbols:
        df = load_symbol(sym, "5m", days_back=180)
        print(f"\n=== {sym} (bars={len(df)}) ===")
        print(f"{'mult':>5} {'adxMax':>6} {'n':>5} {'WR':>6} {'net$':>8} {'PF':>5} {'maxDD$':>8} {'avg$':>7} {'OOSnet$':>9}")
        rows = []
        for p in grid:
            t = run(df, p)
            if t.empty or len(t) < 15: continue
            k = kpis(t); _, oos = split_oos(t); ok = kpis(oos)
            rows.append((p, k, ok))
        for p, k, ok in sorted(rows, key=lambda r: r[1]["net_pnl"], reverse=True):
            print(f"{p.bb_mult:>5.1f} {p.adx_max:>6.0f} {k['n']:>5} {k['win_rate']*100:>5.1f}% "
                  f"{k['net_pnl']:>8,.0f} {k['profit_factor']:>5.2f} {k['max_dd']:>8,.0f} "
                  f"{k['avg_trade']:>7.2f} {ok['net_pnl']:>9,.0f}")
