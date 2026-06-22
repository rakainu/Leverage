"""GAUNTLET Stage 1 — payoff-shape sweep on the regime_mr VWAP-z signal.

Question this answers: can THIS signal ever pay better than the locked ~0.3:1
win/loss shape? We hold the ENTRY fixed at the locked live values (z 1.5,
slope-gate 0.08, accel 3.0) and sweep only the PAYOFF SHAPE:
  timeframe x stop distance x exit model (incl. fuller reversion targets,
  partial-bank + runner, breakeven-after-tp1).

Pools trades across coins (the correct basket unit), zero-fee Lighter costs,
ranks by TOTAL profit (sum of per-trade R = total return per unit risk) with the
win/loss $ ratio shown — the number Rich cares about. Stage 2 will take the
survivors into full IS/OOS + walk-forward + slippage + leverage.

Run:  venv/Scripts/python.exe analysis/scalp_search_2026-05-30/gauntlet_stage1.py
"""
import os
import sys
import itertools

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
import common as K          # noqa: E402
from btengine import simulate  # noqa: E402

COINS = ["BTC", "ETH", "SOL", "HYPE", "ZEC"]   # cached basket; Stage 2 adds fresh + more coins
TFS = ["5m", "15m", "30m", "1h"]
TF_MIN = {**K.TF_MIN, "30m": 30}   # K.TF_MIN lacks 30m

# Locked-live ENTRY (held fixed so Stage 1 isolates payoff shape, not entries)
ENTRY = dict(trend_len=200, slope_lb=20, z_period=30, z_entry=1.5,
             limit_atr=0.25, atr_p=14, accel_mult=3.0, min_slope_pct=0.08)

# Time stop scaled per tf to ~ a few hours / let-winners-run on HTF
MAXBARS = {"5m": 48, "15m": 24, "30m": 16, "1h": 12}

# Payoff-shape axes
SL_ATR = [1.0, 1.5, 2.0, 2.5, 3.0]
# exit configs: label -> dict of regime_mr exit kwargs
def exit_configs():
    cfgs = {}
    # (A) simple fuller-reversion targets — bigger wins by aiming closer to / past VWAP
    for tpf in [0.3, 0.5, 0.7, 1.0]:
        cfgs[f"tp{tpf}"] = dict(tp_frac=tpf)
    # (B) partial-bank + runner: bank tp1_frac of the move, let the rest run to tp2_mult x,
    #     move stop to breakeven after tp1 (the "don't give it back" shape)
    for tp2 in [1.5, 2.5]:
        cfgs[f"runner_tp2x{tp2}"] = dict(tp_frac=0.5, tp1_frac=0.5, tp2_mult=tp2, be_after_tp1=True)
    # (C) breakeven-trail without partial: move to BE once +1R, full target at full reversion
    cfgs["be@1R_tp1.0"] = dict(tp_frac=1.0, be_trigger_r=1.0, be_offset_r=0.0)
    return cfgs


def load_tf(coin, tf):
    """1m/3m/5m/15m via common.load; 30m/1h resampled from native 5m."""
    if tf in ("1m", "3m", "5m", "15m"):
        return K.load(coin, tf)
    rule = {"30m": "30min", "1h": "60min"}[tf]
    df5 = pd.read_parquet(os.path.join(os.path.dirname(__file__), "data", f"okx_{coin}_5m.parquet")).astype(float)
    return df5.resample(rule).agg({"Open": "first", "High": "max", "Low": "min",
                                   "Close": "last", "Volume": "sum"}).dropna()


def pooled_edge(tf, sl_atr, exit_kw):
    """Run regime_mr across coins with these payoff params; pool per-trade returns."""
    params = dict(ENTRY, sl_atr=sl_atr, max_bars=MAXBARS[tf], **exit_kw)
    fn = K.SL.REGISTRY["regime_mr"]
    rows = []   # (entry_time, r)
    weeks = 0.0
    for coin in COINS:
        try:
            df = load_tf(coin, tf)
        except FileNotFoundError:
            continue
        tr = simulate(df, fn(df, side="both", **params), K.LIGHTER, K.RISK, TF_MIN[tf])
        weeks += len(df) * TF_MIN[tf] / (60 * 24 * 7)
        for t in tr:
            r = t.side * (t.exit_price - t.entry_price) / t.entry_price
            rows.append((t.entry_time, r))
    if not rows:
        return None
    rows.sort(key=lambda x: x[0])
    rets = np.array([r[1] for r in rows])
    w, l = rets[rets > 0], rets[rets < 0]
    pf = w.sum() / -l.sum() if l.sum() < 0 else float("inf")
    avg_w = 100 * w.mean() if len(w) else 0.0
    avg_l = 100 * l.mean() if len(l) else 0.0
    ratio = (avg_w / -avg_l) if avg_l < 0 else float("inf")   # win/loss size ratio (Rich's metric)
    # pooled equity curve in R-space for a maxDD read
    eq = np.cumsum(rets); peak = np.maximum.accumulate(eq)
    maxdd_R = float((peak - eq).max()) if len(eq) else 0.0
    n = len(rets)
    be_wr = (-avg_l / (avg_w - avg_l) * 100) if (avg_w - avg_l) else float("nan")  # breakeven WR
    return dict(n=n, tpw=n / (weeks / len(COINS)) if weeks else 0, pf=pf,
                wr=100 * len(w) / n, exp=100 * rets.mean(), sumR=float(rets.sum()),
                avg_w=avg_w, avg_l=avg_l, ratio=ratio, be_wr=be_wr, maxdd_R=maxdd_R)


def main():
    exits = exit_configs()
    results = []
    total = len(TFS) * len(SL_ATR) * len(exits)
    i = 0
    for tf, sl, (elabel, ekw) in itertools.product(TFS, SL_ATR, exits.items()):
        i += 1
        e = pooled_edge(tf, sl, ekw)
        if e and e["n"] >= 30:   # need a sample
            results.append((tf, sl, elabel, e))
        print(f"\r  swept {i}/{total} ...", end="", flush=True)
    print()

    results.sort(key=lambda x: x[3]["sumR"], reverse=True)
    print(f"\n{'rank':>4} {'tf':>4} {'sl':>4} {'exit':>14} {'n':>5} {'t/wk':>6} "
          f"{'PF':>5} {'WR%':>5} {'BE%':>5} {'cush':>5} {'win%':>6} {'loss%':>6} "
          f"{'W/L':>5} {'sumR':>7} {'maxDD_R':>8}")
    print("-" * 110)
    for rank, (tf, sl, el, e) in enumerate(results[:30], 1):
        pf = "inf" if e["pf"] == float("inf") else f"{e['pf']:.2f}"
        rr = "inf" if e["ratio"] == float("inf") else f"{e['ratio']:.2f}"
        cush = e["wr"] - e["be_wr"]
        print(f"{rank:>4} {tf:>4} {sl:>4} {el:>14} {e['n']:>5} {e['tpw']:>6.1f} "
              f"{pf:>5} {e['wr']:>5.0f} {e['be_wr']:>5.0f} {cush:>+5.1f} "
              f"{e['avg_w']:>+6.2f} {e['avg_l']:>+6.2f} {rr:>5} {e['sumR']:>+7.3f} {e['maxdd_R']:>8.3f}")

    # baseline (locked) for reference
    base = pooled_edge("15m", 2.0, dict(tp_frac=0.3))
    if base:
        print("-" * 110)
        rr = "inf" if base["ratio"] == float("inf") else f"{base['ratio']:.2f}"
        cush = base["wr"] - base["be_wr"]
        print(f"{'BASE':>4} {'15m':>4} {2.0:>4} {'tp0.3 (LOCKED)':>14} {base['n']:>5} {base['tpw']:>6.1f} "
              f"{base['pf']:>5.2f} {base['wr']:>5.0f} {base['be_wr']:>5.0f} {cush:>+5.1f} "
              f"{base['avg_w']:>+6.2f} {base['avg_l']:>+6.2f} {rr:>5} {base['sumR']:>+7.3f} {base['maxdd_R']:>8.3f}")
    print("\nPASS bar: W/L >= ~0.8 AND BE% <= ~65 AND sumR clearly > baseline (and OOS/WF in Stage 2).")


if __name__ == "__main__":
    main()
