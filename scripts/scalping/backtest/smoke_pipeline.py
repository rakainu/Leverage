"""End-to-end smoke test of the harness on real SOL 5m data.

Proves: data -> strategy (strat_lib) -> optuna optimize (honest engine) ->
extended metrics + guardrails -> Monte Carlo robustness. Not a strategy claim,
just a wiring check.
"""
from __future__ import annotations
import os
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)  # backtest flat modules

from engine import Costs, RiskCfg            # noqa: E402
from optimizer import optimize               # noqa: E402
from validation import monte_carlo           # noqa: E402

# strat_lib lives in the analysis tree and self-wires its own btengine import
STRAT_DIR = os.path.join(HERE, "..", "analysis", "scalp_search_2026-05-30")
sys.path.insert(0, STRAT_DIR)
import strat_lib as SL                        # noqa: E402

DATA = os.path.join(STRAT_DIR, "data", "okx_SOL_5m.parquet")


def main():
    df = pd.read_parquet(DATA).astype(float)
    print(f"SOL 5m: {len(df)} bars  {df.index[0]} -> {df.index[-1]}")

    costs = Costs(taker_pct=0.0, maker_pct=0.0, slippage_pct=0.05, funding_pct_per_8h=0.01)  # Lighter
    risk = RiskCfg(starting_equity=1000.0, risk_frac=0.01, max_leverage=20,
                   liq_buffer=2.5, compounding=True)

    space = {
        "trend_len": 200, "slope_lb": 20, "limit_atr": 0.25,   # fixed
        "z_period": ("int", 20, 60),
        "z_entry": ("float", 1.0, 3.0),
        "sl_atr": ("float", 1.0, 3.0),
        "tp_frac": ("float", 0.2, 0.8),
        "max_bars": ("int", 6, 24),
    }

    print("\nOptimizing regime_mr (objective=calmar, 60 trials, IS 70% / OOS 30%) ...")
    res = optimize(SL.regime_mr, df, space, costs=costs, risk=risk, tf_minutes=5,
                   side="both", objective="calmar", n_trials=60)

    def line(tag, m):
        pf = m["profit_factor"]; pfs = "inf" if pf == float("inf") else f"{pf:.2f}"
        cal = m["calmar"]; cals = "inf" if cal == float("inf") else f"{cal:.2f}"
        print(f"  {tag:<10} n={m['n']:>4}  PF={pfs:>5}  WR={m['win_rate']:>3.0f}%  "
              f"net={m['net_pct']:>+6.1f}%  CAGR={m['cagr']:>+7.1f}%  Sharpe={m['sharpe']:>5.2f}  "
              f"Calmar={cals:>5}  maxDD={m['max_dd_pct']:>4.1f}%  liq={m['liq_hits']}")

    print(f"\nbest params: {res.best_params}")
    line("IN-SAMPLE", res.is_metrics)
    line("OUT-SAMP", res.oos_metrics)
    print(f"  IS passes guardrails: {res.is_pass} | OOS passes: {res.oos_pass} {res.oos_fail_reasons}")

    # Monte Carlo on the full-sample trades at the winning params
    from engine import simulate
    from metrics import extended_metrics
    sigs = SL.regime_mr(df, side="both", **res.best_params)
    trades = simulate(df, sigs, costs, risk, 5)
    full = extended_metrics(trades, risk.starting_equity, compounding=True)
    line("FULL", full)
    mc = monte_carlo(trades, risk.starting_equity, n=3000, compounding=True)
    print(f"\nMonte Carlo (3000 bootstraps of {full['n']} trades):")
    print(f"  return  median={mc['ret_pct_median']:+.1f}%  p05={mc['ret_pct_p05']:+.1f}%  p95={mc['ret_pct_p95']:+.1f}%")
    print(f"  maxDD   median={mc['maxdd_median']:.1f}%  p95={mc['maxdd_p95']:.1f}%")
    print(f"  prob_profit={mc['prob_profit']:.0%}  prob_ruin={mc['prob_ruin']:.1%}")
    print("\nPIPELINE OK")


if __name__ == "__main__":
    main()
