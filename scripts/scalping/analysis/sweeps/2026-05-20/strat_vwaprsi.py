"""Honest port of 'VWAP-RSI Scalper FINAL v1' (TradingView Pine v6).

Trend-filtered pullback scalp:
  long  = RSI(3) < OS and close > VWAP and close > EMA50   [+ session, max N/day]
  short = RSI(3) > OB and close < VWAP and close < EMA50
  exit  = ATR stop (slATR) / ATR target (tpATR), single position.

Honest fills (same rules as the V3 fix / emavwap port): entry at next_open, TP is a
limit filled on the favorable extreme, SL on the adverse extreme + slippage, and a
bar that straddles BOTH is scored as the SL (conservative). Lighter 0-fee, $7,500
notional. Session filter tested BOTH ways (24h crypto-native vs an ET-equiv window)
since the source's 9-16 ET window is a stock-market artifact.
"""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np
import pandas as pd

from engine import load_symbol, calc_ema, calc_atr, calc_smma
from strategy import kpis
from strat_emavwap import daily_vwap, split_oos

NOTIONAL = 7500.0
SL_SLIP_PCT = 0.0006


def rsi(close: pd.Series, length: int) -> np.ndarray:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    rs = calc_smma(gain, length) / calc_smma(loss, length).replace(0, np.nan)
    return (100 - 100 / (1 + rs)).values


@dataclass
class RVParams:
    rsi_len: int = 3
    rsi_os: float = 35
    rsi_ob: float = 70
    ema_len: int = 50
    atr_len: int = 14
    sl_atr: float = 1.0
    tp_atr: float = 2.0
    max_per_day: int = 3
    session_utc: tuple | None = None   # (start_h, end_h) in UTC, or None = 24h
    commission_pct: float = 0.0


def run(df: pd.DataFrame, p: RVParams, max_lookahead: int = 288):
    c = df["Close"]
    rsiv = rsi(c, p.rsi_len)
    ema = calc_ema(c, p.ema_len).values
    vwap = daily_vwap(df)
    atr = calc_atr(df, p.atr_len).values
    o = df["Open"].values; h = df["High"].values; l = df["Low"].values; cl = c.values
    ts = df.index
    hours = ts.hour.values
    days = ts.normalize()
    n = len(df)

    def in_session(i):
        if p.session_utc is None:
            return True
        s, e = p.session_utc
        return s <= hours[i] < e

    trades = []
    i = p.ema_len + 1
    cur_day = None; today_n = 0
    while i < n - 1:
        if days[i] != cur_day:
            cur_day = days[i]; today_n = 0
        if np.isnan(rsiv[i]) or np.isnan(ema[i]) or np.isnan(atr[i]) or np.isnan(vwap[i]):
            i += 1; continue
        side = None
        if today_n < p.max_per_day and in_session(i):
            if rsiv[i] < p.rsi_os and cl[i] > vwap[i] and cl[i] > ema[i]:
                side = "long"
            elif rsiv[i] > p.rsi_ob and cl[i] < vwap[i] and cl[i] < ema[i]:
                side = "short"
        if side is None:
            i += 1; continue

        entry = float(o[i + 1]); a = float(atr[i]); today_n += 1
        if side == "long":
            sl = entry - a * p.sl_atr; tp = entry + a * p.tp_atr
        else:
            sl = entry + a * p.sl_atr; tp = entry - a * p.tp_atr
        base = NOTIONAL / entry
        exit_price = None; reason = None; jend = min(i + 1 + max_lookahead, n)
        for j in range(i + 1, jend):
            hit_sl = (l[j] <= sl) if side == "long" else (h[j] >= sl)
            hit_tp = (h[j] >= tp) if side == "long" else (l[j] <= tp)
            if hit_sl:
                slip = entry * SL_SLIP_PCT
                exit_price = (sl - slip) if side == "long" else (sl + slip)
                reason = "sl"; break
            if hit_tp:
                exit_price = tp; reason = "tp"; break
        if exit_price is None:
            exit_price = float(cl[jend - 1]); reason = "timeout"; j = jend - 1
        pnl = (exit_price - entry) * base if side == "long" else (entry - exit_price) * base
        fee = (NOTIONAL + (exit_price / entry) * NOTIONAL) * p.commission_pct
        trades.append({"side": side, "entry_ts": ts[i + 1], "exit_reason": reason,
                       "pnl_net": pnl - fee})
        i = j + 1
    return pd.DataFrame(trades)


if __name__ == "__main__":
    import sys
    symbols = sys.argv[1:] or ["ZEC", "SOL"]
    ET_WIN = (13, 21)  # ~9-16 ET in UTC (EDT)
    print("VWAP-RSI scalper — HONEST engine, Lighter 0-fee, $7,500 notional, 180d 5m")
    grid = []
    for sess in [None, ET_WIN]:
        for sl in [1.0, 1.5, 2.0]:
            for tp in [1.5, 2.0, 3.0]:
                grid.append(RVParams(sl_atr=sl, tp_atr=tp, session_utc=sess))
    for sym in symbols:
        df = load_symbol(sym, "5m", days_back=180)
        print(f"\n=== {sym} (bars={len(df)}) ===")
        print(f"{'sess':>5} {'sl':>4} {'tp':>4} {'n':>5} {'WR':>6} {'net$':>9} {'PF':>5} {'maxDD$':>8} {'avg$':>7} {'OOSnet$':>9}")
        rows = []
        for p in grid:
            t = run(df, p)
            if t.empty or len(t) < 15: continue
            k = kpis(t); _, oos = split_oos(t); ok = kpis(oos)
            rows.append((p, k, ok))
        for p, k, ok in sorted(rows, key=lambda r: r[1]["net_pnl"], reverse=True):
            s = "24h" if p.session_utc is None else "ET"
            print(f"{s:>5} {p.sl_atr:>4.1f} {p.tp_atr:>4.1f} {k['n']:>5} {k['win_rate']*100:>5.1f}% "
                  f"{k['net_pnl']:>9,.0f} {k['profit_factor']:>5.2f} {k['max_dd']:>8,.0f} "
                  f"{k['avg_trade']:>7.2f} {ok['net_pnl']:>9,.0f}")
