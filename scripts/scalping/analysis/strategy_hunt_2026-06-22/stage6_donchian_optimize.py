"""Stage 6 — optimize the REAL Donchian on 1h basket, then walk-forward.

Default params (Stage 5) showed the right payoff (2.1) but 1h marginal, BloFin-
negative, 40% DD + a liquidation. The strategy ships with trend filters (off by
default) meant to cut counter-trend trades and bear-market drawdown. Here we let
the optimizer tune channels + tight-stop + the MA/slope filters on the 1h basket,
then put the winner through the SAME walk-forward that killed everything before.
This is the definitive, faithful test of the strategy Rich actually has.
"""
from __future__ import annotations
import os
import sys

import numpy as np
import optuna

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "backtest"))
from engine import Costs, RiskCfg                          # noqa: E402
from optimizer import _suggest                             # noqa: E402
from metrics import extended_metrics                       # noqa: E402
from validation import monte_carlo                         # noqa: E402

from stage2b_basket import load_tf, basket_metrics, COINS, RISK  # noqa: E402
from donchian_millerrh import simulate_donchian            # noqa: E402

optuna.logging.set_verbosity(optuna.logging.WARNING)

LIGHTER = Costs(taker_pct=0.0, maker_pct=0.0, slippage_pct=0.05, funding_pct_per_8h=0.01)
BLOFIN = Costs(taker_pct=0.06, maker_pct=0.02, slippage_pct=0.05, funding_pct_per_8h=0.01)
TF = "1h"; TFM = 60
N_TRIALS = 100
N_FOLDS = 4
MIN_TRADES = 120
HARD_KILL = -1e9
fin = lambda x: 999.0 if (x == float("inf") or (isinstance(x, float) and x != x)) else x

SPACE = dict(
    dc_high=("int", 10, 60), dc_low=("int", 5, 30), dc_stop=("int", 3, 15),
    use_tight_stop=("cat", [False, True]),
    ma_filter=("cat", [False, True]), ma_len=("cat", [50, 100, 200]), ma_type=("cat", ["SMA", "EMA"]),
    slope_filter=("cat", [False, True]), slope_len=("cat", [5, 10, 20]), slope_type="SMA",
)


def run_basket(dfs, costs, params):
    tbc, liq = {}, 0
    for c, df in dfs.items():
        tr = simulate_donchian(df, costs, RISK, TFM, **params)
        tbc[c] = tr
        liq += extended_metrics(tr, RISK.starting_equity, compounding=False)["liq_hits"]
    return tbc, liq


def basket_optimize(dfs_train, n_trials=N_TRIALS, min_trades=MIN_TRADES, seed=42):
    def objective(trial):
        params = {k: _suggest(trial, k, v) for k, v in SPACE.items()}
        try:
            tbc, liq = run_basket(dfs_train, LIGHTER, params)
        except Exception:
            return HARD_KILL
        if liq > 0:
            return HARD_KILL                              # never optimize toward liquidation
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
    dfs = {c: load_tf(c, TF) for c in COINS}
    is_dfs = {c: d.iloc[:int(0.70 * len(d))] for c, d in dfs.items()}
    oos_dfs = {c: d.iloc[int(0.70 * len(d)):] for c, d in dfs.items()}

    print("Optimizing REAL Donchian on 1h basket (IS 70%, 100 trials, filters in play) ...")
    best = basket_optimize(is_dfs)
    print(f"best params: {best}")

    oos_tbc, oos_liq = run_basket(oos_dfs, LIGHTER, best)
    bf_tbc, _ = run_basket(oos_dfs, BLOFIN, best)
    om = basket_metrics(oos_tbc, RISK.starting_equity)
    bm = basket_metrics(bf_tbc, RISK.starting_equity)
    coins_prof = sum(1 for c in COINS if extended_metrics(oos_tbc[c], RISK.starting_equity, compounding=False)["profit_factor"] > 1.0)
    print(f"\nOOS basket: n={om['n']} PF={fin(om['pf']):.2f} WR={om['wr']:.0f}% payoff={fin(om['payoff']):.2f} "
          f"net={om['net_pct']:+.0f}% DD={om['maxdd']:.0f}% liq={oos_liq} coins+={coins_prof}/8 | "
          f"BloFin PF={fin(bm['pf']):.2f} net={bm['net_pct']:+.0f}%")

    full_tbc, full_liq = run_basket(dfs, LIGHTER, best)
    pooled = sorted([t for tr in full_tbc.values() for t in tr], key=lambda t: t.exit_time)
    fm = basket_metrics(full_tbc, RISK.starting_equity)
    mc = monte_carlo(pooled, RISK.starting_equity, n=5000, compounding=True)
    print(f"FULL: n={fm['n']} PF={fin(fm['pf']):.2f} payoff={fin(fm['payoff']):.2f} net={fm['net_pct']:+.0f}% "
          f"DD={fm['maxdd']:.0f}% liq={full_liq}")
    print(f"Monte Carlo (5000x, compounding): ret med={mc['ret_pct_median']:+.0f}% p05={mc['ret_pct_p05']:+.0f}% "
          f"maxDD p95={mc['maxdd_p95']:.0f}% prob_profit={mc['prob_profit']:.0%} prob_ruin={mc['prob_ruin']:.1%}")

    print(f"\nWalk-forward ({N_FOLDS} folds, re-optimized each):")
    passed = 0
    for k in range(N_FOLDS):
        lo, hi = k / N_FOLDS, (k + 1) / N_FOLDS
        train, test = {}, {}
        for c, d in dfs.items():
            seg = d.iloc[int(lo * len(d)):int(hi * len(d))]
            cut = int(len(seg) * 0.70)
            train[c], test[c] = seg.iloc[:cut], seg.iloc[cut:]
        bp = basket_optimize(train, n_trials=70, min_trades=max(30, MIN_TRADES // N_FOLDS))
        ttbc, _ = run_basket(test, LIGHTER, bp)
        m = basket_metrics(ttbc, RISK.starting_equity)
        if m is None:
            print(f"  fold {k}: no trades"); continue
        ok = (m["pf"] >= 1.2 and m["payoff"] >= 1.0 and m["maxdd"] <= 30 and m["net_pct"] > 0)
        passed += ok
        print(f"  fold {k}: n={m['n']:>3} PF={fin(m['pf']):.2f} payoff={fin(m['payoff']):.2f} "
              f"net={m['net_pct']:>+5.0f}% DD={m['maxdd']:>3.0f}% filt(ma={bp['ma_filter']},slope={bp['slope_filter']}) "
              f"{'PASS' if ok else 'fail'}")
    verdict = "ROBUST" if passed >= int(np.ceil(0.75 * N_FOLDS)) else "FRAGILE"
    print(f"\nVERDICT (real Donchian, faithful): {passed}/{N_FOLDS} folds profitable -> {verdict}")


if __name__ == "__main__":
    main()
