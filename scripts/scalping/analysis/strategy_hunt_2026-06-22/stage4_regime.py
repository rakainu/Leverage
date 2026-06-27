"""Stage 4 — regime-gated Donchian. Does sitting out chop fix the walk-forward?

Stage 3 killed raw 1h Donchian: 1/4 folds (great in trends, bled in chop). Here
the search can switch on ADX and Kaufman-Efficiency-Ratio regime gates. If the
edge is real-but-regime-dependent, gating should lift walk-forward robustness;
if Donchian has no durable edge at all, gating won't save it. Either answer is
useful. Same honest pipeline: basket optimize on 1h IS, then re-optimized
walk-forward as the verdict.
"""
from __future__ import annotations
import os
import sys

import numpy as np
import optuna

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "backtest"))
from optimizer import _suggest                              # noqa: E402
from validation import monte_carlo                          # noqa: E402
from metrics import extended_metrics                        # noqa: E402

from stage2b_basket import (load_tf, run_basket, basket_metrics, COINS, LIGHTER,  # noqa: E402
                            BLOFIN, RISK, TF_MIN, MIN_BASKET_TRADES, HARD_KILL)
from donchian_family import donchian_breakout               # noqa: E402

optuna.logging.set_verbosity(optuna.logging.WARNING)

TF = "1h"
N_TRIALS = 90
N_FOLDS = 4

# regime-gated Donchian search space (adds adx_min, er_min, er_len)
SPACE = dict(
    ema_slope_lb=3, atr_p=14, adx_p=14,
    entry_n=("int", 10, 50), sl_atr=("float", 0.8, 2.5), tp_atr=("float", 2.0, 6.0),
    ema_len=("cat", [50, 100, 200]), vol_mult=("cat", [0.0, 1.0, 1.2]),
    atr_min_pct=("float", 0.0, 0.8), trail_atr=("cat", [0.0, 2.0, 3.0]),
    max_bars=("cat", [0, 48, 96]),
    adx_min=("cat", [0, 15, 20, 25, 30]),                  # trend-strength gate
    er_min=("float", 0.0, 0.5), er_len=("cat", [10, 20, 30]),  # chop gate
)


def basket_optimize(dfs_train, tfm, n_trials=N_TRIALS, min_trades=MIN_BASKET_TRADES, seed=42):
    def objective(trial):
        params = {k: _suggest(trial, k, v) for k, v in SPACE.items()}
        try:
            tbc, liq = run_basket(donchian_breakout, dfs_train, params, LIGHTER, tfm)
        except Exception:
            return HARD_KILL
        if liq > 0:
            return HARD_KILL
        m = basket_metrics(tbc, RISK.starting_equity)
        if m is None or m["n"] < min_trades:
            return HARD_KILL + (0 if m is None else m["n"])
        val = m["calmar"] if m["calmar"] != float("inf") else 1e6
        over = m["maxdd"] - 25.0
        if over > 0:
            val -= over * abs(val or 1.0) * 0.1 + over
        return float(val)

    study = optuna.create_study(direction="maximize", sampler=optuna.samplers.TPESampler(seed=seed))
    study.optimize(objective, n_trials=n_trials)
    fixed = {k: v for k, v in SPACE.items() if not isinstance(v, tuple)}
    return {**fixed, **study.best_params}


def main():
    fin = lambda x: 999.0 if (x == float("inf") or (isinstance(x, float) and x != x)) else x
    dfs = {c: load_tf(c, TF) for c in COINS}
    is_dfs = {c: d.iloc[:int(0.70 * len(d))] for c, d in dfs.items()}
    oos_dfs = {c: d.iloc[int(0.70 * len(d)):] for c, d in dfs.items()}
    tfm = TF_MIN[TF]

    print("Optimizing regime-gated Donchian on 1h basket (IS 70%, 90 trials) ...")
    best = basket_optimize(is_dfs, tfm)
    print(f"best params: {best}")
    print(f"  regime gates chosen -> adx_min={best.get('adx_min')}  er_min={best.get('er_min'):.3f}  er_len={best.get('er_len')}")

    oos_tbc, oos_liq = run_basket(donchian_breakout, oos_dfs, best, LIGHTER, tfm)
    bf_tbc, _ = run_basket(donchian_breakout, oos_dfs, best, BLOFIN, tfm)
    om = basket_metrics(oos_tbc, RISK.starting_equity)
    bm = basket_metrics(bf_tbc, RISK.starting_equity)
    per_coin = {c: extended_metrics(oos_tbc[c], RISK.starting_equity, compounding=False)["profit_factor"] for c in COINS}
    coins_prof = sum(1 for v in per_coin.values() if v > 1.0)
    print(f"\nOOS basket: n={om['n']} PF={fin(om['pf']):.2f} WR={om['wr']:.0f}% payoff={fin(om['payoff']):.2f} "
          f"net={om['net_pct']:+.0f}% DD={om['maxdd']:.0f}% liq={oos_liq} coins+={coins_prof}/8 | "
          f"BloFin PF={fin(bm['pf']):.2f} net={bm['net_pct']:+.0f}%")

    # full-sample Monte Carlo
    full_tbc, full_liq = run_basket(donchian_breakout, dfs, best, LIGHTER, tfm)
    pooled = sorted([t for tr in full_tbc.values() for t in tr], key=lambda t: t.exit_time)
    fm = basket_metrics(full_tbc, RISK.starting_equity)
    mc = monte_carlo(pooled, RISK.starting_equity, n=5000, compounding=True)
    print(f"FULL: n={fm['n']} PF={fin(fm['pf']):.2f} payoff={fin(fm['payoff']):.2f} net={fm['net_pct']:+.0f}% "
          f"DD={fm['maxdd']:.0f}% liq={full_liq}")
    print(f"Monte Carlo (5000x, compounding): ret med={mc['ret_pct_median']:+.0f}% p05={mc['ret_pct_p05']:+.0f}% "
          f"p95={mc['ret_pct_p95']:+.0f}%  maxDD p95={mc['maxdd_p95']:.0f}%  "
          f"prob_profit={mc['prob_profit']:.0%} prob_ruin={mc['prob_ruin']:.1%}")

    # ---- decisive: walk-forward, re-optimized per fold ----
    print(f"\nWalk-forward ({N_FOLDS} folds, regime-gated, re-optimized each):")
    passed = 0
    for k in range(N_FOLDS):
        lo, hi = k / N_FOLDS, (k + 1) / N_FOLDS
        train, test = {}, {}
        for c, d in dfs.items():
            seg = d.iloc[int(lo * len(d)):int(hi * len(d))]
            cut = int(len(seg) * 0.70)
            train[c], test[c] = seg.iloc[:cut], seg.iloc[cut:]
        bp = basket_optimize(train, tfm, n_trials=70, min_trades=max(40, MIN_BASKET_TRADES // N_FOLDS))
        ttbc, _ = run_basket(donchian_breakout, test, bp, LIGHTER, tfm)
        m = basket_metrics(ttbc, RISK.starting_equity)
        if m is None:
            print(f"  fold {k}: no trades"); continue
        ok = (m["pf"] >= 1.2 and m["payoff"] >= 1.0 and m["maxdd"] <= 30 and m["net_pct"] > 0)
        passed += ok
        print(f"  fold {k}: n={m['n']:>3} PF={fin(m['pf']):.2f} payoff={fin(m['payoff']):.2f} "
              f"net={m['net_pct']:>+5.0f}% DD={m['maxdd']:>3.0f}% adx_min={bp.get('adx_min')} "
              f"er_min={bp.get('er_min'):.2f} {'PASS' if ok else 'fail'}")
    verdict = "ROBUST" if passed >= int(np.ceil(0.75 * N_FOLDS)) else "FRAGILE"
    print(f"\nVERDICT: {passed}/{N_FOLDS} folds profitable -> {verdict}   (raw Donchian was 1/4)")


if __name__ == "__main__":
    main()
