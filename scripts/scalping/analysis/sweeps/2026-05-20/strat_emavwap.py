"""Honest port of 'Ultimate Scalping Strategy v2.3' (EMA cross + VWAP + ATR exits).

External candidate (TradingView Pine v5). Ported into the HONEST engine to test
whether it has any edge — NOT to reproduce TradingView Strategy Tester numbers
(which fill optimistically + can repaint). Honesty rules carried from the V3 fix
(see project_v3_entry_fill_phantom):
  - entries fill at next bar OPEN (market on the signal, no idealized fill)
  - TP is a limit: fills when the FAVORABLE extreme reaches it
  - SL fills when the ADVERSE extreme reaches it, plus slippage
  - if one bar straddles BOTH sl and tp, assume SL (conservative — can't know order)
  - opposite-signal exit fills at next open
  - sizing: fixed $250 x 30 = $7,500 notional (comparable to V3); Lighter 0 fee

Entry (Pine):
  long  = crossover(emaFast, emaSlow) and close > vwap   [+ optional engulf/volume]
  short = crossunder(emaFast, emaSlow) and close < vwap   [+ optional engulf/volume]
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
import numpy as np
import pandas as pd

from engine import load_symbol, calc_ema, calc_atr
from strategy import kpis

NOTIONAL = 7500.0
SL_SLIP_PCT = 0.0006  # 0.06% past stop trigger (matches live-calibrated value)


@dataclass
class EVParams:
    fast: int = 9
    slow: int = 21
    atr_len: int = 14
    sl_mult: float = 1.5
    tp_mult: float = 2.0
    use_price_action: bool = False
    use_volume: bool = False
    vol_ma_len: int = 20
    exit_on_opposite: bool = True
    allow_longs: bool = True
    allow_shorts: bool = True
    commission_pct: float = 0.0   # Lighter


def daily_vwap(df: pd.DataFrame) -> np.ndarray:
    """Session VWAP, reset at each UTC date change (matches Pine ta.vwap default)."""
    tp = (df["High"] + df["Low"] + df["Close"]) / 3.0
    vol = df["Volume"].values.astype(float)
    tpv = tp.values * vol
    day = df.index.normalize()  # UTC midnight buckets
    out = np.empty(len(df))
    cum_tpv = cum_v = 0.0
    cur = None
    for i in range(len(df)):
        if day[i] != cur:
            cur = day[i]; cum_tpv = cum_v = 0.0
        cum_tpv += tpv[i]; cum_v += vol[i]
        out[i] = cum_tpv / cum_v if cum_v > 0 else df["Close"].values[i]
    return out


def prepare(df: pd.DataFrame, p: EVParams) -> pd.DataFrame:
    df = df.copy()
    df["emaF"] = calc_ema(df["Close"], p.fast).values
    df["emaS"] = calc_ema(df["Close"], p.slow).values
    df["vwap"] = daily_vwap(df)
    df["atr"] = calc_atr(df, p.atr_len).values
    df["volMA"] = df["Volume"].rolling(p.vol_ma_len).mean().values
    return df


def run(df: pd.DataFrame, p: EVParams, max_lookahead: int = 288):
    df = prepare(df, p)
    o = df["Open"].values; h = df["High"].values; l = df["Low"].values; c = df["Close"].values
    emaF = df["emaF"].values; emaS = df["emaS"].values; vwap = df["vwap"].values
    atr = df["atr"].values; volMA = df["volMA"].values; vol = df["Volume"].values
    op = df["Open"].values
    ts = df.index
    n = len(df)

    def bull_engulf(i): return c[i] > o[i] and c[i-1] < o[i-1] and c[i] > o[i-1] and o[i] < c[i-1]
    def bear_engulf(i): return c[i] < o[i] and c[i-1] > o[i-1] and c[i] < o[i-1] and o[i] > c[i-1]

    trades = []
    i = 1
    while i < n - 1:
        if np.isnan(emaS[i]) or np.isnan(emaS[i-1]) or np.isnan(atr[i]) or np.isnan(vwap[i]):
            i += 1; continue
        cross_up = emaF[i] > emaS[i] and emaF[i-1] <= emaS[i-1]
        cross_dn = emaF[i] < emaS[i] and emaF[i-1] >= emaS[i-1]
        long_c = cross_up and c[i] > vwap[i] and (not p.use_price_action or bull_engulf(i)) and (not p.use_volume or vol[i] > volMA[i])
        short_c = cross_dn and c[i] < vwap[i] and (not p.use_price_action or bear_engulf(i)) and (not p.use_volume or vol[i] > volMA[i])

        side = None
        if long_c and p.allow_longs: side = "long"
        elif short_c and p.allow_shorts: side = "short"
        if side is None:
            i += 1; continue

        entry = float(op[i + 1])              # honest: market fill at next open
        a = float(atr[i])
        if side == "long":
            sl = entry - a * p.sl_mult; tp = entry + a * p.tp_mult
        else:
            sl = entry + a * p.sl_mult; tp = entry - a * p.tp_mult
        base = NOTIONAL / entry

        exit_price = None; reason = None; jend = min(i + 1 + max_lookahead, n)
        for j in range(i + 1, jend):
            hit_sl = (l[j] <= sl) if side == "long" else (h[j] >= sl)
            hit_tp = (h[j] >= tp) if side == "long" else (l[j] <= tp)
            if hit_sl:  # conservative: SL wins a straddled bar
                slip = entry * SL_SLIP_PCT
                exit_price = (sl - slip) if side == "long" else (sl + slip)
                reason = "sl"; break
            if hit_tp:
                exit_price = tp; reason = "tp"; break
            # opposite-signal exit at NEXT open
            if p.exit_on_opposite:
                opp = (emaF[j] < emaS[j] and emaF[j-1] >= emaS[j-1] and c[j] < vwap[j]) if side == "long" \
                      else (emaF[j] > emaS[j] and emaF[j-1] <= emaS[j-1] and c[j] > vwap[j])
                if opp and j + 1 < n:
                    exit_price = float(op[j + 1]); reason = "opposite"; break
        if exit_price is None:
            exit_price = float(c[jend - 1]); reason = "timeout"; j = jend - 1

        pnl = (exit_price - entry) * base if side == "long" else (entry - exit_price) * base
        fee = (NOTIONAL + (exit_price / entry) * NOTIONAL) * p.commission_pct
        trades.append({"side": side, "entry_ts": ts[i + 1], "entry": entry, "exit": exit_price,
                       "exit_reason": reason, "pnl_net": pnl - fee})
        i = j + 1   # net position: no overlap, resume after exit

    tdf = pd.DataFrame(trades)
    return tdf


def split_oos(tdf, frac=0.30):
    if tdf.empty: return tdf, tdf
    cut = int(len(tdf) * (1 - frac))
    return tdf.iloc[:cut], tdf.iloc[cut:]


if __name__ == "__main__":
    import itertools, sys
    symbols = sys.argv[1:] or ["ZEC", "SOL"]
    print("EMA+VWAP scalp — HONEST engine, Lighter 0-fee, $7,500 notional, 180d 5m")
    grid = []
    for fast, slow in [(9, 21), (8, 21), (5, 20)]:
        for sl_m in [1.0, 1.5, 2.0]:
            for tp_m in [1.5, 2.0, 3.0]:
                grid.append(EVParams(fast=fast, slow=slow, sl_mult=sl_m, tp_mult=tp_m))
    for sym in symbols:
        df = load_symbol(sym, "5m", days_back=180)
        print(f"\n=== {sym}  bars={len(df)} ===")
        print(f"{'ema':>7} {'sl':>4} {'tp':>4} {'n':>5} {'WR':>6} {'net$':>9} {'PF':>5} {'maxDD$':>8} {'avg$':>7} {'OOSnet$':>9}")
        rows = []
        for p in grid:
            t = run(df, p)
            if t.empty or len(t) < 20: continue
            k = kpis(t.rename(columns={}))
            _, oos = split_oos(t); ok = kpis(oos)
            rows.append((p, k, ok))
        for p, k, ok in sorted(rows, key=lambda r: r[1]["net_pnl"], reverse=True):
            print(f"{p.fast:>3}/{p.slow:<3} {p.sl_mult:>4.1f} {p.tp_mult:>4.1f} {k['n']:>5} "
                  f"{k['win_rate']*100:>5.1f}% {k['net_pnl']:>9,.0f} {k['profit_factor']:>5.2f} "
                  f"{k['max_dd']:>8,.0f} {k['avg_trade']:>7.2f} {ok['net_pnl']:>9,.0f}")
