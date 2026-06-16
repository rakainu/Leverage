"""What IS the 1h day-trade strategy? Full characterization + lever sweep.

Strategy under test (the validated best cell from run_1h.py):
  ENTRY  : 20-bar breakout (close breaks prior-20 high/low), 1h bars
  GATE   : regime filter -> only long if EMA50>EMA200 & EMA200 rising; mirror short
  EXIT   : "let winners run" -> 1.5*ATR stop, scale 50% at +1R, move runner to
           breakeven, trail the rest 3*ATR behind the close; 24h (24-bar) time stop
  SIZE   : fixed-fractional risk to the hard stop (risk_frac of equity per trade)
  VENUE  : Lighter (zero fee) is the deploy target; BloFin shown for contrast

This file does NOT just vary the 3 knobs Rich named. It isolates every lever that
actually moves a trend-runner: size, leverage, trail, stop width, scale-out point,
time stop, entry family, venue -- plus IS/OOS + 2x-slip robustness on the baseline.
Each sweep changes ONE thing off the baseline so the cause is unambiguous.
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd

ENGINE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "sol_strategy_2026-05-30"))
sys.path.insert(0, ENGINE_DIR)
import btengine as bt  # noqa: E402
from run_1h import load_1h, prep, regime_ok, COINS, TF_MIN  # noqa: E402

LIGHTER = bt.Costs(taker_pct=0.0, maker_pct=0.0, slippage_pct=0.02, funding_pct_per_8h=0.01)
LIGHTER_2XSLIP = bt.Costs(taker_pct=0.0, maker_pct=0.0, slippage_pct=0.04, funding_pct_per_8h=0.01)
BLOFIN = bt.Costs()

# ---- Baseline config (the validated winner) ----
BASE = dict(entry="breakout", gate=True, sl_atr=1.5, tp_atr=1.5,
            tp1_frac=0.5, be_after_tp1=True, trail_atr=3.0, max_bars=24)


def gen(df, *, entry, gate, sl_atr, tp_atr, tp1_frac, be_after_tp1, trail_atr, max_bars):
    C = df["Close"].values; A = df["atr"].values
    e20 = df["ema20"].values
    hh = df["hh20"].values; ll = df["ll20"].values
    rows = list(df.itertuples())
    sigs = []
    for i in range(200, len(df) - 1):
        atr_i = A[i]
        if not np.isfinite(atr_i) or atr_i <= 0:
            continue
        side = 0
        if entry == "pullback":
            if C[i] > e20[i] and C[i - 1] <= e20[i - 1]:
                side = +1
            elif C[i] < e20[i] and C[i - 1] >= e20[i - 1]:
                side = -1
        else:  # breakout
            if C[i] > hh[i]:
                side = +1
            elif C[i] < ll[i]:
                side = -1
        if side == 0:
            continue
        if gate and not regime_ok(rows[i], side):
            continue
        sigs.append(bt.Signal(
            i=i, side=side, sl_dist=sl_atr * atr_i, tp_dist=tp_atr * atr_i,
            entry_style="market", max_bars=max_bars,
            tp1_frac=tp1_frac, be_after_tp1=be_after_tp1,
            trail_atr=(trail_atr * atr_i) if trail_atr > 0 else 0.0))
    return sigs


def run_basket(data, cfg, costs, risk_frac=0.01, compounding=False, start=1000.0,
               max_lev=30.0, side_filter=None, slice_=None):
    pooled = []
    final_eq = start
    for c in COINS:
        df = data[c]
        if slice_ == "is":
            df, _ = bt.split_is_oos(df, 0.70)
        elif slice_ == "oos":
            _, df = bt.split_is_oos(df, 0.70)
        sigs = gen(df, **cfg)
        if side_filter is not None:
            sigs = [s for s in sigs if s.side == side_filter]
        risk = bt.RiskCfg(starting_equity=start, risk_frac=risk_frac,
                          compounding=compounding, max_leverage=max_lev)
        tr = bt.simulate(df, sigs, costs, risk, TF_MIN)
        pooled += tr
        if tr:
            final_eq = tr[-1].equity_after  # per-coin; only meaningful single-coin
    return pooled


def M(trades, weeks, start=1000.0):
    m = bt.metrics(trades, start)
    m["tpw"] = m["n"] / weeks if weeks else 0
    m["hold"] = np.mean([t.bars_held for t in trades]) if trades else 0.0
    return m


def row(label, m):
    pf = m["profit_factor"]; pfs = "inf" if pf == float("inf") else f"{pf:5.2f}"
    return (f"{label:<24} n={m['n']:>4} PF={pfs} WR={m['win_rate']:4.0f}% "
            f"avgR={m['avg_r']:+.3f} net={m['net_pct']:+7.1f}% maxDD={m['max_dd_pct']:5.1f}% "
            f"lev={m['max_leverage_used']:4.1f}x liq={m['liq_hits']:>2} "
            f"tpw={m['tpw']:4.1f} hold={m['hold']:4.1f}h")


def main():
    data = {c: prep(load_1h(c)) for c in COINS}
    span = (data["SOL"].index.max() - data["SOL"].index.min()).days
    wk = span / 7.0
    WB = wk * len(COINS)  # basket weeks
    bh = {c: (data[c]["Close"].iloc[-1] / data[c]["Close"].iloc[0] - 1) * 100 for c in COINS}
    print(f"# 1h DAY-TRADE STRATEGY | {COINS} | {span}d (~{wk:.0f}wk) | Lighter zero-fee unless noted")
    print(f"# Buy&hold over window: " + "  ".join(f"{c} {bh[c]:+.0f}%" for c in COINS))
    print(f"# Baseline = {BASE}\n")

    def sweep(title, variants, **fixed):
        print(f"\n{'='*108}\n{title}\n{'='*108}")
        for label, over in variants:
            cfg = {**BASE, **{k: v for k, v in over.items() if k in BASE}}
            kw = {k: v for k, v in {**fixed, **over}.items() if k not in BASE}
            tr = run_basket(data, cfg, kw.pop("costs", LIGHTER), **kw)
            print("  " + row(label, M(tr, WB)))

    # 1. WHAT IT IS — baseline, pooled + per-coin + long/short
    print(f"\n{'='*108}\n1. CHARACTER — baseline config, Lighter zero-fee\n{'='*108}")
    base_tr = run_basket(data, BASE, LIGHTER)
    print("  " + row("[POOLED] all", M(base_tr, WB)))
    print("  " + row("  long only", M(run_basket(data, BASE, LIGHTER, side_filter=+1), WB)))
    print("  " + row("  short only", M(run_basket(data, BASE, LIGHTER, side_filter=-1), WB)))
    for c in COINS:
        tr = bt.simulate(data[c], gen(data[c], **BASE), LIGHTER,
                         bt.RiskCfg(starting_equity=1000.0, risk_frac=0.01, compounding=False), TF_MIN)
        print("      " + row(c, M(tr, wk)))

    # 2. SIZE (risk per trade), compounding ON, real $3k account
    print(f"\n{'='*108}\n2. SIZE — risk_frac sweep, COMPOUNDING on $3,000 (final equity = what you'd have)\n{'='*108}")
    for rf in (0.005, 0.01, 0.02, 0.03, 0.05):
        tr = run_basket(data, BASE, LIGHTER, risk_frac=rf, compounding=True, start=3000.0)
        m = M(tr, WB, start=3000.0)
        final = tr[-1].equity_after if tr else 3000.0
        print("  " + row(f"risk {rf*100:.1f}%/trade", m) + f"  -> ${final:,.0f} on $3k")

    # 3. LEVERAGE CAP — is it a profit knob or just a liquidation knob here?
    sweep("3. LEVERAGE — max_leverage cap @ risk 2% compounding (watch lev-used / liq)",
          [(f"cap {L}x", dict(max_lev=float(L))) for L in (3, 5, 10, 20, 30, 50)],
          risk_frac=0.02, compounding=True, start=3000.0)

    # 4. TRAIL — the core runner lever
    sweep("4. TRAIL — ATR distance behind the close (0 = no trail, ride to time stop)",
          [(f"trail {t}ATR", dict(trail_atr=t)) for t in (0.0, 1.5, 2.0, 3.0, 4.0, 5.0, 6.0)])

    # 5. STOP WIDTH (tp tracks sl to keep ~1:1 TP1 trigger)
    sweep("5. STOP WIDTH — sl_atr (tp_atr tracks it)",
          [(f"sl {s}ATR", dict(sl_atr=s, tp_atr=s)) for s in (1.0, 1.5, 2.0, 2.5, 3.0)])

    # 6. SCALE-OUT — how much to bank at +1R vs let run
    sweep("6. SCALE-OUT — tp1_frac banked at +1R (1.0 = full exit, no runner)",
          [(f"bank {int(f*100)}%@1R", dict(tp1_frac=f)) for f in (0.3, 0.5, 0.7, 1.0)])

    # 7. TIME STOP
    sweep("7. TIME STOP — max_bars (1h bars)",
          [(f"{b}h stop", dict(max_bars=b)) for b in (12, 24, 36, 48, 72)])

    # 8. ENTRY FAMILY + GATE
    sweep("8. ENTRY / GATE",
          [("breakout gateON", dict(entry="breakout", gate=True)),
           ("breakout gateOFF", dict(entry="breakout", gate=False)),
           ("pullback gateON", dict(entry="pullback", gate=True)),
           ("pullback gateOFF", dict(entry="pullback", gate=False))])

    # 9. ROBUSTNESS on the baseline
    print(f"\n{'='*108}\n9. ROBUSTNESS — baseline config\n{'='*108}")
    print("  " + row("full window (Lighter)", M(run_basket(data, BASE, LIGHTER), WB)))
    print("  " + row("in-sample 70%", M(run_basket(data, BASE, LIGHTER, slice_="is"), WB * 0.7)))
    print("  " + row("out-sample 30%", M(run_basket(data, BASE, LIGHTER, slice_="oos"), WB * 0.3)))
    print("  " + row("2x slippage", M(run_basket(data, BASE, LIGHTER_2XSLIP), WB)))
    print("  " + row("BloFin fees (0.06%)", M(run_basket(data, BASE, BLOFIN), WB)))


if __name__ == "__main__":
    main()
