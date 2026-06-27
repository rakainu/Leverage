"""Stage 3 — the overfit gauntlet for the two 1h finalists.

A single IS/OOS split looked great. This is the decisive test:

  WALK-FORWARD: cut the history into folds; on each fold RE-OPTIMIZE the basket on
  the train window and score the untouched test window. If the edge only exists
  when we tune on the whole sample, it dies here. A real edge survives re-tuning.

  MONTE CARLO: bootstrap-resample the pooled trades thousands of times -> a
  distribution of outcomes (median return, 5th-pct drawdown, prob of ruin).
  A good headline means nothing if the unlucky path liquidates you.
"""
from __future__ import annotations
import ast
import os
import sys

import numpy as np
import pandas as pd
import optuna

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "backtest"))
from optimizer import _suggest                          # noqa: E402
from validation import monte_carlo                      # noqa: E402

from stage2b_basket import (load_tf, run_basket, basket_metrics, FAMILIES, SPACES,  # noqa: E402
                            COINS, LIGHTER, BLOFIN, RISK, TF_MIN, MIN_BASKET_TRADES, HARD_KILL)

optuna.logging.set_verbosity(optuna.logging.WARNING)

FINALISTS = ["donchian_breakout", "reclaim_pullback"]
TF = "1h"
N_TRIALS = 60
N_FOLDS = 4


def basket_optimize(fn, space, dfs_train, tfm, n_trials=N_TRIALS, seed=42):
    def objective(trial):
        params = {k: _suggest(trial, k, v) for k, v in space.items()}
        try:
            tbc, liq = run_basket(fn, dfs_train, params, LIGHTER, tfm)
        except Exception:
            return HARD_KILL
        if liq > 0:
            return HARD_KILL
        m = basket_metrics(tbc, RISK.starting_equity)
        if m is None or m["n"] < max(40, MIN_BASKET_TRADES // N_FOLDS):
            return HARD_KILL + (0 if m is None else m["n"])
        val = m["calmar"] if m["calmar"] != float("inf") else 1e6
        over = m["maxdd"] - 25.0
        if over > 0:
            val -= over * abs(val or 1.0) * 0.1 + over
        return float(val)

    study = optuna.create_study(direction="maximize", sampler=optuna.samplers.TPESampler(seed=seed))
    study.optimize(objective, n_trials=n_trials)
    fixed = {k: v for k, v in space.items() if not isinstance(v, tuple)}
    return {**fixed, **study.best_params}


def walk_forward(fam, tf=TF, n_folds=N_FOLDS):
    fn = FAMILIES[fam]; space = SPACES[fam]; tfm = TF_MIN[tf]
    dfs = {c: load_tf(c, tf) for c in COINS}
    n_min = min(len(d) for d in dfs.values())
    bounds = np.linspace(0, 1, n_folds + 1)
    out = []
    for k in range(n_folds):
        lo, hi = bounds[k], bounds[k + 1]
        train, test = {}, {}
        for c, d in dfs.items():
            seg = d.iloc[int(lo * len(d)):int(hi * len(d))]
            cut = int(len(seg) * 0.70)
            train[c], test[c] = seg.iloc[:cut], seg.iloc[cut:]
        best = basket_optimize(fn, space, train, tfm)
        ttbc, _ = run_basket(fn, test, best, LIGHTER, tfm)
        m = basket_metrics(ttbc, RISK.starting_equity)
        out.append(dict(fold=k, m=m, params=best))
    return out


def main():
    df2b = pd.read_csv(os.path.join(HERE, "stage2b_results.csv"))
    fin = lambda x: 999.0 if (x == float("inf") or (isinstance(x, float) and x != x)) else x

    for fam in FINALISTS:
        row = df2b[(df2b.family == fam) & (df2b.tf == TF)].iloc[0]
        best = ast.literal_eval(row["params"]) if isinstance(row["params"], str) else row["params"]
        print(f"\n{'='*78}\n{fam}  (1h)   best params: {best}\n{'='*78}")

        # ---- Monte Carlo on full-sample pooled trades ----
        dfs = {c: load_tf(c, TF) for c in COINS}
        tbc, liq = run_basket(FAMILIES[fam], dfs, best, LIGHTER, TF_MIN[TF])
        pooled = sorted([t for tr in tbc.values() for t in tr], key=lambda t: t.exit_time)
        fm = basket_metrics(tbc, RISK.starting_equity)
        print(f"  FULL basket: n={fm['n']} PF={fin(fm['pf']):.2f} WR={fm['wr']:.0f}% "
              f"payoff={fin(fm['payoff']):.2f} net={fm['net_pct']:+.0f}% DD={fm['maxdd']:.0f}% liq={liq}")
        mc = monte_carlo(pooled, RISK.starting_equity, n=5000, compounding=True)
        print(f"  Monte Carlo (5000x, compounding): return med={mc['ret_pct_median']:+.0f}% "
              f"p05={mc['ret_pct_p05']:+.0f}% p95={mc['ret_pct_p95']:+.0f}%  "
              f"maxDD p95={mc['maxdd_p95']:.0f}%  prob_profit={mc['prob_profit']:.0%} "
              f"prob_ruin={mc['prob_ruin']:.1%}")

        # ---- Walk-forward (re-optimize each fold) ----
        wf = walk_forward(fam)
        passed = 0
        print(f"  Walk-forward ({N_FOLDS} folds, re-optimized each):")
        for f in wf:
            m = f["m"]
            if m is None:
                print(f"    fold {f['fold']}: no trades"); continue
            ok = (m["pf"] >= 1.2 and m["payoff"] >= 1.0 and m["maxdd"] <= 30 and m["net_pct"] > 0)
            passed += ok
            print(f"    fold {f['fold']}: n={m['n']:>3} PF={fin(m['pf']):.2f} payoff={fin(m['payoff']):.2f} "
                  f"net={m['net_pct']:>+5.0f}% DD={m['maxdd']:>3.0f}% {'PASS' if ok else 'fail'}")
        verdict = "ROBUST" if passed >= int(np.ceil(0.75 * N_FOLDS)) else "FRAGILE"
        print(f"  WALK-FORWARD VERDICT: {passed}/{N_FOLDS} folds profitable -> {verdict}")


if __name__ == "__main__":
    main()
