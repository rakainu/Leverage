"""Honest port of 'Z-Score Mean Reversion Pro' (TradingView Pine v6).

Statistical mean-reversion — the target family for a zero-fee venue:
  z = (close - SMA(window)) / stdev(window)
  long  = z < -z_thresh  [+ RSI<os, BB-width>min, close>EMA200 if filters on]
  short = z > +z_thresh   [+ RSI>ob, BB-width>min, close<EMA200 if filters on]
  exit  = ATR stop (sl_atr) / ATR target (tp_atr), single position, cooldown bars.

Honest fills (same rules as the other ports): entry at next_open, TP limit on the
favorable extreme, SL on the adverse extreme + slippage, straddled bar scored as SL
(conservative). Lighter 0-fee, $7,500 notional. EMA200 trend filter swept on/off.
"""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np
import pandas as pd

from engine import load_symbol, calc_ema, calc_atr
from strategy import kpis
from strat_vwaprsi import rsi
from strat_emavwap import split_oos
from strat_bbmr import adx

NOTIONAL = 7500.0
SL_SLIP_PCT = 0.0006


@dataclass
class ZParams:
    window: int = 80
    z_thresh: float = 2.5
    rsi_len: int = 14
    rsi_os: float = 30
    rsi_ob: float = 70
    use_rsi: bool = True
    bb_len: int = 20
    bb_mult: float = 2.0
    bb_width_min: float = 0.02
    use_bb: bool = True
    ema_len: int = 200
    use_ema: bool = True
    atr_len: int = 14
    sl_atr: float = 2.0
    tp_atr: float = 3.0
    cooldown: int = 5
    commission_pct: float = 0.0
    # --- Per-symbol suitability (regime) gate ---
    use_adx: bool = False        # only fade when ADX <= adx_max (ranging, not trending)
    adx_len: int = 14
    adx_max: float = 30.0
    # "market" = enter next_open when CLOSE's z crosses the threshold (original).
    # "limit"  = rest a limit at the z-band; fills on any intrabar TOUCH at the band
    #            price (more fills, better entry — but eats trend-throughs honestly).
    entry_mode: str = "market"


def run(df: pd.DataFrame, p: ZParams, max_lookahead: int = 288):
    c = df["Close"]
    mean = c.rolling(p.window).mean()
    std = c.rolling(p.window).std(ddof=1)          # ta.stdev(.., false) = sample
    z = ((c - mean) / std.replace(0, np.nan)).values
    mean_v = mean.values; std_v = std.values
    rsiv = rsi(c, p.rsi_len)
    bb_basis = c.rolling(p.bb_len).mean()
    bb_std = c.rolling(p.bb_len).std(ddof=0)        # BB default = population
    bb_width = ((2 * p.bb_mult * bb_std) / bb_basis).values  # (upper-lower)/basis
    ema = calc_ema(c, p.ema_len).values
    atr = calc_atr(df, p.atr_len).values
    adxv = adx(df, p.adx_len) if p.use_adx else None
    o = df["Open"].values; h = df["High"].values; l = df["Low"].values; cl = c.values
    ts = df.index
    n = len(df)

    trades = []
    i = max(p.window, p.ema_len, p.atr_len) + 1
    last_entry = -10**9
    while i < n - 1:
        if any(np.isnan(x) for x in (z[i], rsiv[i], bb_width[i], ema[i], atr[i])):
            i += 1; continue
        if i - last_entry < p.cooldown:
            i += 1; continue
        bb_ok = (not p.use_bb) or bb_width[i] > p.bb_width_min
        regime_ok = (not p.use_adx) or (not np.isnan(adxv[i]) and adxv[i] <= p.adx_max)
        side = None; entry = None
        if bb_ok and regime_ok:
            rsi_lo = (not p.use_rsi) or rsiv[i] < p.rsi_os
            rsi_hi = (not p.use_rsi) or rsiv[i] > p.rsi_ob
            ema_lo = (not p.use_ema) or cl[i] > ema[i]
            ema_hi = (not p.use_ema) or cl[i] < ema[i]
            if p.entry_mode == "limit":
                band_lo = mean_v[i] - p.z_thresh * std_v[i]   # z = -thresh price level
                band_hi = mean_v[i] + p.z_thresh * std_v[i]
                if l[i] <= band_lo and rsi_lo and ema_lo:
                    side = "long"; entry = float(band_lo)      # resting buy limit fill @ band
                elif h[i] >= band_hi and rsi_hi and ema_hi:
                    side = "short"; entry = float(band_hi)
            else:  # market: close's z beyond threshold, fill next open
                if z[i] < -p.z_thresh and rsi_lo and ema_lo:
                    side = "long"
                elif z[i] > p.z_thresh and rsi_hi and ema_hi:
                    side = "short"
                if side is not None:
                    entry = float(o[i + 1])
        if side is None:
            i += 1; continue

        a = float(atr[i]); last_entry = i
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
        trades.append({"side": side, "entry_ts": ts[i + 1], "exit_reason": reason, "pnl_net": pnl - fee})
        i = j + 1
    return pd.DataFrame(trades)


if __name__ == "__main__":
    import sys
    symbols = sys.argv[1:] or ["ZEC", "SOL"]
    print("Z-Score Mean Reversion — HONEST engine, Lighter 0-fee, $7,500 notional, 180d 5m")
    grid = []
    for z in [2.0, 2.5, 3.0]:
        for sl in [1.5, 2.0]:
            for tp in [2.0, 3.0]:
                for ema in [True, False]:
                    grid.append(ZParams(z_thresh=z, sl_atr=sl, tp_atr=tp, use_ema=ema))
    for sym in symbols:
        df = load_symbol(sym, "5m", days_back=180)
        print(f"\n=== {sym} (bars={len(df)}) ===")
        print(f"{'z':>4} {'sl':>4} {'tp':>4} {'ema':>4} {'n':>5} {'WR':>6} {'net$':>9} {'PF':>5} {'maxDD$':>8} {'avg$':>7} {'OOSnet$':>9}")
        rows = []
        for p in grid:
            t = run(df, p)
            if t.empty or len(t) < 15: continue
            k = kpis(t); _, oos = split_oos(t); ok = kpis(oos)
            rows.append((p, k, ok))
        for p, k, ok in sorted(rows, key=lambda r: r[1]["net_pnl"], reverse=True):
            print(f"{p.z_thresh:>4.1f} {p.sl_atr:>4.1f} {p.tp_atr:>4.1f} {str(p.use_ema):>4} {k['n']:>5} "
                  f"{k['win_rate']*100:>5.1f}% {k['net_pnl']:>9,.0f} {k['profit_factor']:>5.2f} "
                  f"{k['max_dd']:>8,.0f} {k['avg_trade']:>7.2f} {ok['net_pnl']:>9,.0f}")
