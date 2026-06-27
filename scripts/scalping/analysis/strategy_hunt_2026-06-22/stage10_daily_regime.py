"""Stage 10 — Donchian gated by a SLOW daily BTC regime (the canonical bull/bear line).

Stage 9's gate (EMA on the trading TF) was too fast and whipsawed. Here the regime
is BTC's DAILY close vs its daily EMA — a proper medium-term market filter. Only
go long while BTC is above it. Reports 6-fold walk-forward (per-regime) AND the
3-year aggregate so a regime-conditional strategy is judged on its whole arc, not
just a binary fold count.
"""
from __future__ import annotations
import os
import sys

import numpy as np
import pandas as pd
import optuna

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "backtest"))
from engine import ema                                  # noqa: E402
sys.path.insert(0, HERE)
from stage8_fair_retest import (load_hist, basket_metrics, COINS, LIGHTER, BLOFIN,  # noqa: E402
                                RISK, TF_MIN, fin)
from stage9_regime_gated import run_basket, basket_optimize, gate_for, fold_bh  # noqa: E402

optuna.logging.set_verbosity(optuna.logging.WARNING)
REGIME_EMA_DAYS = 100   # BTC daily close > EMA100(daily) = medium-term uptrend


def btc_daily_regime():
    btc = load_hist("BTC", "1h")
    dclose = btc["Close"].resample("1D").last().dropna()
    reg = dclose > ema(dclose, REGIME_EMA_DAYS)
    return dclose.index, reg


def run_tf(tf):
    tfm = TF_MIN[tf]
    dfs = {c: load_hist(c, tf) for c in COINS}
    ridx, reg = btc_daily_regime()
    gates = {c: gate_for(d, ridx, reg) for c, d in dfs.items()}
    pct_on = np.mean([g.mean() for g in gates.values()]) * 100
    print(f"\n{'='*78}\n{tf}  | BTC daily>EMA{REGIME_EMA_DAYS}d regime ON {pct_on:.0f}% of the time\n{'='*78}")

    NF = 6
    print(f"Walk-forward {NF} folds (daily-regime gated):")
    passed = 0
    fold_nets = []
    for k in range(NF):
        lo, hi = k / NF, (k + 1) / NF
        tr, te, trg, teg = {}, {}, {}, {}
        for c, d in dfs.items():
            a, b = int(lo * len(d)), int(hi * len(d))
            seg = d.iloc[a:b]; cut = int(len(seg) * 0.7)
            tr[c], te[c] = seg.iloc[:cut], seg.iloc[cut:]
            g = gates[c]; trg[c], teg[c] = g[a:a + cut], g[a + cut:b]
        bp = basket_optimize(tr, tfm, n_trials=60, min_trades=max(20, 200 // NF), gates=trg)
        ttbc, _ = run_basket(te, LIGHTER, bp, tfm, teg)
        fm = basket_metrics(ttbc, RISK.starting_equity)
        bh = fold_bh(te); d0 = list(te.values())[0]
        if fm is None or fm["n"] == 0:
            passed += (bh < 0); fold_nets.append(0.0)
            print(f"  fold{k} regime={bh:>+4.0f}% | FLAT (gate off) {'OK avoided bear' if bh < 0 else 'missed'}")
            continue
        ok = (fm["pf"] >= 1.2 and fm["payoff"] >= 1.0 and fm["maxdd"] <= 30 and fm["net_pct"] > 0)
        passed += ok; fold_nets.append(fm["net_pct"])
        print(f"  fold{k} [{d0.index[0].date()}..{d0.index[-1].date()}] regime={bh:>+4.0f}% | "
              f"n={fm['n']:>3} PF={fin(fm['pf']):.2f} payoff={fin(fm['payoff']):.2f} net={fm['net_pct']:>+5.0f}% "
              f"DD={fm['maxdd']:>3.0f}% {'PASS' if ok else 'fail'}")
    print(f"VERDICT {tf}: {passed}/{NF} -> {'ROBUST' if passed >= int(np.ceil(0.75*NF)) else 'FRAGILE'}  "
          f"| sum fold net = {sum(fold_nets):+.0f}% (fixed-risk, per-coin pooled)")

    # 3-year aggregate at a single fixed config (IS-optimized), gated
    is_dfs = {c: d.iloc[:int(0.5 * len(d))] for c, d in dfs.items()}
    isg = {c: gates[c][:int(0.5 * len(d))] for c, d in dfs.items()}
    best = basket_optimize(is_dfs, tfm, n_trials=120, min_trades=120, gates=isg)
    full, fliq = run_basket(dfs, LIGHTER, best, tfm, gates)
    m = basket_metrics(full, RISK.starting_equity)
    bf, _ = run_basket(dfs, BLOFIN, best, tfm, gates); bm = basket_metrics(bf, RISK.starting_equity)
    print(f"  FULL 3y (IS-opt config, gated): n={m['n']} PF={fin(m['pf']):.2f} payoff={fin(m['payoff']):.2f} "
          f"net={m['net_pct']:+.0f}% DD={m['maxdd']:.0f}% Sharpe={m['sharpe']:.2f} liq={fliq} | "
          f"BloFin PF={fin(bm['pf']):.2f} net={bm['net_pct']:+.0f}%")
    # per-year net
    allt = sorted([t for tr in full.values() for t in tr], key=lambda t: t.exit_time)
    by_year = {}
    for t in allt:
        by_year.setdefault(t.exit_time.year, 0.0)
        by_year[t.exit_time.year] += t.pnl_usd
    yr = "  ".join(f"{y}:{v/RISK.starting_equity*100:+.0f}%" for y, v in sorted(by_year.items()))
    print(f"  per-year net (fixed-risk pooled): {yr}")
    print(f"  best: {best}")


def main():
    print(f"DAILY-REGIME-GATED Donchian (BTC daily>EMA{REGIME_EMA_DAYS}d) — 3y basket")
    for tf in ["1h", "4h"]:
        run_tf(tf)


if __name__ == "__main__":
    main()
