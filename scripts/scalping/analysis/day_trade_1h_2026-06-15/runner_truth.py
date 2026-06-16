"""Prove the baseline exit is inert, then test a REAL runner.

Finding: run_1h.py's "RUN" exit set tp1_frac=0.5 + trail=3ATR but NO tp2_dist,
so btengine (line 267) collapses it to a single 1.5ATR-TP full exit. The strat
that was validated is really: regime-gated breakout, 1.5ATR stop / 1.5ATR TP (~1:1),
24h time stop. No scaling, no trailing. This file:
  A) prints the exit-reason mix to prove no trail/tp1/tp2 fires at baseline
  B) builds a GENUINE runner (tp2_dist set so the multi-leg path activates) and
     sweeps the runner levers, so we see if 'let winners run' actually helps.
"""
from __future__ import annotations
import os, sys
from collections import Counter
import numpy as np
import pandas as pd

ENGINE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "sol_strategy_2026-05-30"))
sys.path.insert(0, ENGINE_DIR)
import btengine as bt  # noqa: E402
from run_1h import load_1h, prep, regime_ok, COINS, TF_MIN  # noqa: E402

LIGHTER = bt.Costs(taker_pct=0.0, maker_pct=0.0, slippage_pct=0.02, funding_pct_per_8h=0.01)


def gen(df, *, entry="breakout", gate=True, sl_atr=1.5, tp_atr=1.5,
        tp1_frac=1.0, be_after_tp1=False, trail_atr=0.0, tp2_atr=0.0, max_bars=24):
    C = df["Close"].values; A = df["atr"].values
    e20 = df["ema20"].values; hh = df["hh20"].values; ll = df["ll20"].values
    rows = list(df.itertuples()); sigs = []
    for i in range(200, len(df) - 1):
        a = A[i]
        if not np.isfinite(a) or a <= 0:
            continue
        side = 0
        if entry == "pullback":
            if C[i] > e20[i] and C[i - 1] <= e20[i - 1]: side = +1
            elif C[i] < e20[i] and C[i - 1] >= e20[i - 1]: side = -1
        else:
            if C[i] > hh[i]: side = +1
            elif C[i] < ll[i]: side = -1
        if side == 0 or (gate and not regime_ok(rows[i], side)):
            continue
        sigs.append(bt.Signal(
            i=i, side=side, sl_dist=sl_atr * a, tp_dist=tp_atr * a, entry_style="market",
            max_bars=max_bars, tp1_frac=tp1_frac, be_after_tp1=be_after_tp1,
            trail_atr=(trail_atr * a) if trail_atr > 0 else 0.0,
            tp2_dist=(tp2_atr * a) if tp2_atr > 0 else 0.0))
    return sigs


def basket(data, costs=LIGHTER, **cfg):
    pooled = []
    for c in COINS:
        risk = bt.RiskCfg(starting_equity=1000.0, risk_frac=0.01, compounding=False)
        pooled += bt.simulate(data[c], gen(data[c], **cfg), costs, risk, TF_MIN)
    return pooled


def M(tr, wk):
    m = bt.metrics(tr, 1000.0)
    m["tpw"] = m["n"] / wk if wk else 0
    m["hold"] = np.mean([t.bars_held for t in tr]) if tr else 0.0
    return m


def row(label, m):
    pf = m["profit_factor"]; pfs = "inf" if pf == float("inf") else f"{pf:5.2f}"
    return (f"{label:<28} n={m['n']:>4} PF={pfs} WR={m['win_rate']:4.0f}% "
            f"avgR={m['avg_r']:+.3f} net={m['net_pct']:+7.1f}% maxDD={m['max_dd_pct']:5.1f}% hold={m['hold']:4.1f}h")


def main():
    data = {c: prep(load_1h(c)) for c in COINS}
    wk = (data["SOL"].index.max() - data["SOL"].index.min()).days / 7.0 * len(COINS)

    # A) Prove the "validated" baseline exit is inert
    print("="*100)
    print("A. EXIT-REASON MIX of the 'validated' baseline (tp1=0.5, trail=3ATR, NO tp2)")
    print("="*100)
    inert = dict(tp1_frac=0.5, be_after_tp1=True, trail_atr=3.0, tp2_atr=0.0)
    tr = basket(data, **inert)
    rc = Counter(t.exit_reason for t in tr)
    print("  " + row("validated baseline", M(tr, wk)))
    print("  exit reasons:", dict(rc),
          "\n  -> no 'tp1'/'tp2'/'trail_sl' => scale-out & 3ATR trail NEVER fire. It's a 1:1 TP/stop.\n")

    # B) Strip it to the honest truth: plain 1:1, no runner params at all
    print("="*100)
    print("B. HONEST BASELINE = regime-gated breakout, 1.5ATR stop / 1.5ATR TP, 24h time stop")
    print("="*100)
    plain = dict(tp1_frac=1.0, trail_atr=0.0, tp2_atr=0.0)
    print("  " + row("plain 1:1 (what it really is)", M(basket(data, **plain), wk)) + "\n")

    # C) A GENUINE runner: bank 50% at +1R, runner rides tp2 far out + trail + BE
    print("="*100)
    print("C. REAL RUNNER — bank 50% @ +1R, runner to tp2 with BE+trail (multi-leg ACTIVE)")
    print("="*100)
    for tp2 in (3.0, 4.0, 6.0, 8.0):
        for trail in (2.0, 3.0, 4.0):
            cfg = dict(tp1_frac=0.5, be_after_tp1=True, trail_atr=trail, tp2_atr=tp2)
            tr = basket(data, **cfg)
            rc = Counter(t.exit_reason for t in tr)
            print("  " + row(f"tp2={tp2}ATR trail={trail}ATR", M(tr, wk)) +
                  f"  reasons={{tp1:{rc.get('tp1',0)},tp2:{rc.get('tp2',0)},trail:{rc.get('trail_sl',0)},be:{rc.get('be',0)},sl:{rc.get('sl',0)},time:{rc.get('time',0)}}}")

    # D) Scale fraction on the real runner (now it should actually matter)
    print("\n" + "="*100)
    print("D. SCALE-OUT fraction on a real runner (tp2=6ATR, trail=3ATR) — now non-trivial?")
    print("="*100)
    for f in (0.3, 0.5, 0.7):
        print("  " + row(f"bank {int(f*100)}%@1R", M(basket(data, tp1_frac=f, be_after_tp1=True, trail_atr=3.0, tp2_atr=6.0), wk)))


if __name__ == "__main__":
    main()
