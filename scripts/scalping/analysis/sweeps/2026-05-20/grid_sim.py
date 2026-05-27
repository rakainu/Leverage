"""Honest simulator for 'Grid Strategy with MA' (Seungdori_, TradingView Pine v5).

Long-only, no-stop grid: MA center, 15 levels spaced band_mult*ATR apart. Buy a
parcel when price crosses DOWN through a level (resting buy limit); close that
parcel when price rallies ONE grid step above (resting sell limit). Up to 15
concurrent longs. NO stop loss.

A normal backtest LIES about grids: ~100% win rate on closed parcels + a pretty
equity curve, while the real risk hides as UNREALIZED loss on the open stack
during a downtrend. So this sim reports the tail-risk metrics that matter:
  - realized net (the seductive number)
  - max concurrent open parcels  + peak capital deployed
  - worst aggregate MARK-TO-MARKET drawdown (the truth)
  - parcels still open (underwater) at window end
  - whether price broke below the lowest grid (the catastrophe trigger)

Fills are maker limits AT each level (how a grid actually rests orders) — fair on
a zero-fee venue. Entry = level value at the down-cross; exit = level-above value.
"""
from __future__ import annotations
import sys
import numpy as np
import pandas as pd

from engine import load_symbol, calc_ema, calc_atr, calc_smma

CASH_PER_PARCEL = 10_000.0   # matches the Pine default_qty_value
MA_LEN = 100
ATR_LEN = 100
BAND_MULT = 2.5
K_RANGE = list(range(-7, 8))  # entry levels: discount_7..premium_7 (15 levels)


def build_levels(df: pd.DataFrame, ma_len=MA_LEN, atr_len=ATR_LEN, band=BAND_MULT):
    ma = calc_smma  # placeholder; we use SMA below
    src = (df["Open"] + df["High"] + df["Low"] + df["Close"]) / 4.0  # ohlc4
    main = src.rolling(ma_len).mean().values                         # SMA(100)
    atr = calc_atr(df, atr_len).values
    levels = {}
    for k in range(-8, 9):
        raw = main + atr * (band * k)
        levels[k] = calc_ema(pd.Series(raw, index=df.index), 5).values
    return main, atr, levels


def run(df: pd.DataFrame, fee=0.0):
    main, atr, lv = build_levels(df)
    high = df["High"].values; low = df["Low"].values; close = df["Close"].values
    n = len(df)

    open_entry = {k: None for k in K_RANGE}   # entry price if parcel open at slot k
    realized = 0.0
    closed = wins = 0
    peak_concurrent = 0
    peak_deployed = 0.0
    equity_peak = 0.0
    max_dd = 0.0                 # worst (realized+unrealized) drawdown
    broke_below_lowest = False
    worst_unreal = 0.0

    start = max(MA_LEN, ATR_LEN) + 5
    for i in range(start, n):
        # entries: low crosses DOWN through level k (was >= last bar, now <)
        for k in K_RANGE:
            Lk = lv[k][i]; Lk1 = lv[k][i-1]
            if np.isnan(Lk) or np.isnan(Lk1):
                continue
            if open_entry[k] is None and low[i] < Lk and low[i-1] >= Lk1:
                open_entry[k] = Lk          # maker fill at the level
        # exits: high crosses UP through the level one step above (k+1)
        for k in K_RANGE:
            if open_entry[k] is None:
                continue
            Lup = lv[k+1][i]; Lup1 = lv[k+1][i-1]
            if np.isnan(Lup) or np.isnan(Lup1):
                continue
            if high[i] > Lup and high[i-1] <= Lup1:
                entry = open_entry[k]
                base = CASH_PER_PARCEL / entry
                pnl = (Lup - entry) * base
                pnl -= (CASH_PER_PARCEL + (Lup/entry)*CASH_PER_PARCEL) * fee
                realized += pnl
                closed += 1
                if pnl > 0: wins += 1
                open_entry[k] = None

        # mark-to-market the open stack
        n_open = sum(1 for k in K_RANGE if open_entry[k] is not None)
        unreal = 0.0
        for k in K_RANGE:
            if open_entry[k] is not None:
                base = CASH_PER_PARCEL / open_entry[k]
                unreal += (close[i] - open_entry[k]) * base
        peak_concurrent = max(peak_concurrent, n_open)
        peak_deployed = max(peak_deployed, n_open * CASH_PER_PARCEL)
        worst_unreal = min(worst_unreal, unreal)
        equity = realized + unreal
        equity_peak = max(equity_peak, equity)
        max_dd = min(max_dd, equity - equity_peak)
        if low[i] < lv[-7][i]:
            broke_below_lowest = True

    # final open bag
    open_bag = 0.0; open_n = 0
    for k in K_RANGE:
        if open_entry[k] is not None:
            base = CASH_PER_PARCEL / open_entry[k]
            open_bag += (close[-1] - open_entry[k]) * base
            open_n += 1

    return {
        "closed": closed, "win_rate": (wins/closed*100 if closed else 0),
        "realized": realized,
        "peak_concurrent": peak_concurrent,
        "peak_deployed": peak_deployed,
        "worst_unreal_dd": worst_unreal,
        "max_equity_dd": max_dd,
        "open_at_end": open_n, "open_bag_unreal": open_bag,
        "broke_below_lowest": broke_below_lowest,
    }


if __name__ == "__main__":
    symbols = sys.argv[1:] or ["ZEC", "SOL"]
    print(f"GRID (long-only, no-SL) — HONEST sim, Lighter 0-fee, ${CASH_PER_PARCEL:,.0f}/parcel, "
          f"MA{MA_LEN} ATR{ATR_LEN} band{BAND_MULT}x, 180d 5m")
    for sym in symbols:
        df = load_symbol(sym, "5m", days_back=180)
        r = run(df, fee=0.0)
        print(f"\n=== {sym} (bars={len(df)}) ===")
        print(f"  SEDUCTIVE: realized net ${r['realized']:,.0f}  closed={r['closed']}  "
              f"win_rate={r['win_rate']:.1f}%")
        print(f"  TRUTH    : worst aggregate UNREALIZED drawdown ${r['worst_unreal_dd']:,.0f}")
        print(f"             max equity (real+unreal) drawdown   ${r['max_equity_dd']:,.0f}")
        print(f"             peak concurrent parcels {r['peak_concurrent']}  "
              f"peak capital deployed ${r['peak_deployed']:,.0f}")
        print(f"             open & underwater at end: {r['open_at_end']} parcels, "
              f"${r['open_bag_unreal']:,.0f} unrealized")
        print(f"             price broke below lowest grid? {r['broke_below_lowest']}")
