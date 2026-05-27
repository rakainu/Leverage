"""Honest port of 'VWAP Mean Reversion Strategy v6' (TradingView Pine v6).

Volume-weighted-abs-deviation band fade + RSI + volume-spike filter:
  long  = src crosses DOWN through (vwMean - 2*vwAbsDev) and RSI < 25 and not volume-spike
  short = src crosses UP   through (vwMean + 2*vwAbsDev) and RSI > 65 and not volume-spike
  exit  = fixed % stop / TP at the moving vwMean (basis). Single position.

Prior flagged: exit-AT-the-mean is the losing shape from BB-MR and RVWAP. 0.5% stop
is on the slippage floor. Honest fills: entry next_open, TP-at-mean on favorable
extreme, % stop on adverse extreme + slippage, straddle = stop. Lighter 0-fee.
"""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np
import pandas as pd

from engine import load_symbol
from strategy import kpis
from strat_vwaprsi import rsi
from strat_emavwap import split_oos

NOTIONAL = 7500.0
SL_SLIP_PCT = 0.0006


@dataclass
class VWParams:
    vwap_len: int = 60
    rsi_len: int = 14
    rsi_os: float = 25
    rsi_ob: float = 65
    entry_mult: float = 2.0
    stop_pct: float = 0.005
    vol_lookback: int = 20
    vol_mult: float = 3.0
    use_vol_filter: bool = True
    commission_pct: float = 0.0


def vw_basis_dev(df, length):
    src = df["Close"].values.astype(float)
    vol = df["Volume"].values.astype(float)
    sv = pd.Series(src * vol).rolling(length).sum().values
    v = pd.Series(vol).rolling(length).sum().values
    basis = sv / v
    n = len(df)
    dev = np.full(n, np.nan)
    for t in range(length - 1, n):
        b = basis[t]
        if np.isnan(b): continue
        w_v = vol[t-length+1:t+1]
        w_s = src[t-length+1:t+1]
        sv_ = w_v.sum()
        if sv_ > 0:
            dev[t] = (w_v * np.abs(w_s - b)).sum() / sv_
    return basis, dev


def run(df, p, basis, dev, rsiv, avgvol):
    src = df["Close"].values.astype(float)
    o = df["Open"].values; h = df["High"].values; l = df["Low"].values
    vol = df["Volume"].values.astype(float)
    ts = df.index; n = len(df)
    trades = []
    i = p.vwap_len + 1
    while i < n - 1:
        if any(np.isnan(x) for x in (basis[i], dev[i], rsiv[i], basis[i-1], dev[i-1])):
            i += 1; continue
        lower = basis[i] - dev[i]*p.entry_mult; upper = basis[i] + dev[i]*p.entry_mult
        lower1 = basis[i-1] - dev[i-1]*p.entry_mult; upper1 = basis[i-1] + dev[i-1]*p.entry_mult
        vol_ok = (not p.use_vol_filter) or not (vol[i] > avgvol[i]*p.vol_mult)
        long_sig = src[i] < lower and src[i-1] >= lower1 and rsiv[i] < p.rsi_os and vol_ok
        short_sig = src[i] > upper and src[i-1] <= upper1 and rsiv[i] > p.rsi_ob and vol_ok
        side = "long" if long_sig else ("short" if short_sig else None)
        if side is None:
            i += 1; continue
        entry = float(o[i+1]); base = NOTIONAL/entry
        stop = entry*(1-p.stop_pct) if side == "long" else entry*(1+p.stop_pct)
        exit_price=None; reason=None; jend=min(i+1+288, n)
        for j in range(i+1, jend):
            hit_sl = (l[j] <= stop) if side == "long" else (h[j] >= stop)
            tp = basis[j]
            hit_tp = (h[j] >= tp) if side == "long" else (l[j] <= tp)
            if hit_sl:
                slip = entry*SL_SLIP_PCT
                exit_price = (stop-slip) if side == "long" else (stop+slip)
                reason="sl"; break
            if not np.isnan(tp) and hit_tp:
                exit_price = tp; reason="tp"; break
        if exit_price is None:
            exit_price = float(src[jend-1]); reason="timeout"; j=jend-1
        pnl = (exit_price-entry)*base if side=="long" else (entry-exit_price)*base
        fee = (NOTIONAL + (exit_price/entry)*NOTIONAL)*p.commission_pct
        trades.append({"side":side,"exit_reason":reason,"pnl_net":pnl-fee})
        i = j+1
    return pd.DataFrame(trades)


if __name__ == "__main__":
    import sys
    symbols = sys.argv[1:] or ["SOL","BTC","ETH","ZEC"]
    print("VWAP-AbsDev Mean Reversion — HONEST engine, Lighter 0-fee, $7,500 notional, 180d 5m")
    print(f"{'sym':>4} {'mult':>4} {'stop%':>5} {'n':>5} {'WR':>6} {'net$':>8} {'PF':>5} {'maxDD$':>8} {'avg$':>7} {'OOS$':>8}")
    for sym in symbols:
        df = load_symbol(sym,"5m",days_back=180)
        basis, dev = vw_basis_dev(df, 60)
        rsiv = rsi(df["Close"], 14)
        avgvol = df["Volume"].rolling(20).mean().values
        for mult in [2.0, 3.0]:
            for stop in [0.005, 0.01]:
                t = run(df, VWParams(entry_mult=mult, stop_pct=stop), basis, dev, rsiv, avgvol)
                if t.empty or len(t) < 15:
                    print(f"{sym:>4} {mult:>4.1f} {stop*100:>4.1f}%   (<15)"); continue
                k=kpis(t); _,oos=split_oos(t); ok=kpis(oos)
                print(f"{sym:>4} {mult:>4.1f} {stop*100:>4.1f}% {k['n']:>5} {k['win_rate']*100:>5.1f}% "
                      f"{k['net_pnl']:>8,.0f} {k['profit_factor']:>5.2f} {k['max_dd']:>8,.0f} {k['avg_trade']:>7.2f} {ok['net_pnl']:>8,.0f}")
