"""Port of Rich's Neo_TP_TrendPullback_5m_v1.4 — a WITH-TREND PULLBACK (the
promising family). Core logic ported faithfully:
  - regime: EMA20/50 aligned + price the right side of EMA50 + EMA50 sloping +
    ADX>=20.
  - pullback: price touched the EMA20-50 zone within the last 12 bars, not too
    deep (>= EMA50 - 0.5*ATR), then a bar CLOSES back across the fast EMA in the
    trend direction (close>EMAfast & close>open for long).
  - stop: ~1.2*ATR (capped 1.8). exits: TP1 1R (50%), TP2 2R, breakeven after TP1.
  - filters: ADX>=20; skip if EMA20/50 crossed within last 20 bars.

Tested on basket + HYPE, 5m (native) + 15m, IS/OOS, zero-fee.

Run: ../../venv/Scripts/python.exe neo_trendpullback.py
"""
from __future__ import annotations
import os, sys, glob
import numpy as np
import pandas as pd

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "sol_strategy_2026-05-30"))
from btengine import Signal, simulate, Costs, RiskCfg, atr, ema, adx  # noqa: E402

LIGHTER = Costs(taker_pct=0.0, maker_pct=0.0, slippage_pct=0.05, funding_pct_per_8h=0.01)
RISK = RiskCfg(starting_equity=1000.0, risk_frac=0.01, max_leverage=20,
               liq_buffer=2.5, compounding=True)
TF_MIN = {"5m": 5, "15m": 15}


def load(coin, tf):
    df = pd.read_parquet(os.path.join(HERE, "data", f"{coin}_5m.parquet")).astype(float)
    if tf == "5m":
        return df
    return df.resample("15min").agg({"Open": "first", "High": "max", "Low": "min",
                                     "Close": "last", "Volume": "sum"}).dropna()


def _barssince(mask):
    out = np.full(len(mask), 1_000_000)
    last = -1_000_000
    for i, m in enumerate(mask):
        if m:
            last = i
        out[i] = i - last
    return out


def signals(df, ema_f=20, ema_s=50, adx_thr=20.0, max_pb=12, depth_atr=0.5,
            stop_atr=1.5, tp1_r=1.0, tp2_r=2.0):
    O, H, L, C = (df[c].values for c in ("Open", "High", "Low", "Close"))
    ef = ema(df["Close"], ema_f).values
    es = ema(df["Close"], ema_s).values
    a = atr(df, 14).values
    adxv = adx(df, 14).values
    es5 = ema(df["Close"], ema_s).shift(5).values

    long_touch = (ef > es) & (L <= ef) & (H >= es)
    short_touch = (ef < es) & (H >= ef) & (L <= es)
    bs_long = _barssince(np.nan_to_num(long_touch, nan=0).astype(bool))
    bs_short = _barssince(np.nan_to_num(short_touch, nan=0).astype(bool))
    cross = ((ef > es) & (np.r_[False, (ef[:-1] <= es[:-1])])) | \
            ((ef < es) & (np.r_[False, (ef[:-1] >= es[:-1])]))
    bs_cross = _barssince(cross)

    sigs = []
    for i in range(1, len(df)):
        if any(np.isnan(x) for x in (ef[i], es[i], a[i], adxv[i], es5[i])) or a[i] <= 0:
            continue
        if adxv[i] < adx_thr or bs_cross[i] < 20:
            continue
        up = C[i] > es[i] and ef[i] > es[i] and es[i] > es5[i]
        dn = C[i] < es[i] and ef[i] < es[i] and es[i] < es5[i]
        side = 0
        if up and 1 <= bs_long[i] <= max_pb and L[i] >= es[i] - depth_atr * a[i] \
                and C[i] > ef[i] and C[i] > O[i]:
            side = 1
        elif dn and 1 <= bs_short[i] <= max_pb and H[i] <= es[i] + depth_atr * a[i] \
                and C[i] < ef[i] and C[i] < O[i]:
            side = -1
        if side == 0:
            continue
        sl = stop_atr * a[i]
        sigs.append(Signal(i=i, side=side, sl_dist=sl, tp_dist=tp1_r * sl,
                           entry_style="market", tp1_frac=0.5, tp2_dist=tp2_r * sl,
                           be_after_tp1=True, max_bars=48))
    return sigs


def stat(trades):
    if not trades:
        return dict(n=0, wr=0, net=0.0, pf=0.0)
    w = [t.pnl_usd for t in trades if t.pnl_usd > 0]
    l = [t.pnl_usd for t in trades if t.pnl_usd <= 0]
    pf = sum(w) / -sum(l) if sum(l) < 0 else float("inf")
    return dict(n=len(trades), wr=len(w) / len(trades) * 100, net=sum(w) + sum(l), pf=pf)


def run(coins, tf, lo, hi, **kw):
    allt = []
    for c in coins:
        df = load(c, tf); a, b = int(len(df) * lo), int(len(df) * hi)
        sub = df.iloc[a:b]
        allt += simulate(sub, signals(sub, **kw), LIGHTER, RISK, TF_MIN[tf])
    return stat(allt)


def main():
    coins = sorted(os.path.basename(p).split("_")[0]
                   for p in glob.glob(os.path.join(HERE, "data", "*_5m.parquet")))
    for label, cs in (("BASKET", coins), ("HYPE", ["HYPE"])):
        print(f"\n=== {label} ===")
        print(f"{'tf':4} {'stop':>4} {'IS net':>8} {'IS PF':>6} {'IS n':>5} "
              f"{'OOS net':>8} {'OOS PF':>7} {'OOS n':>5} {'OOS wr':>7}")
        for tf in ("5m", "15m"):
            for sl in (1.2, 1.8):
                i = run(cs, tf, 0.0, 0.7, stop_atr=sl)
                o = run(cs, tf, 0.7, 1.0, stop_atr=sl)
                ipf = "inf" if i["pf"] == float("inf") else f"{i['pf']:.2f}"
                opf = "inf" if o["pf"] == float("inf") else f"{o['pf']:.2f}"
                flag = " <" if (i["net"] > 0 and o["net"] > 0) else ""
                print(f"{tf:4} {sl:>4} {i['net']:>+8.0f} {ipf:>6} {i['n']:>5} "
                      f"{o['net']:>+8.0f} {opf:>7} {o['n']:>5} {o['wr']:>6.0f}%{flag}")
    print("\nread: '<' = positive IS AND OOS. with-trend pullback is the promising "
          "family — does it actually clear the bar?")


if __name__ == "__main__":
    main()
