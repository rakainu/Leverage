"""Stage 2 — optimize the momentum/breakout/reversal families (payoff>1 candidates).

Fade families are skipped on purpose: Stage 1 showed they're the small-win/big-loss
trap. Here we give the OTHER profile a fair shot with optuna, then judge on
OUT-OF-SAMPLE + guardrails. regime_mr is included only as the benchmark to beat.

Search: Lighter zero-fee, fixed sizing (compounding off for fair comparison),
objective = Calmar, IS 70% / OOS 30%. For each OOS survivor we also re-run with
BloFin fees so we know which strategies survive losing Lighter access.
"""
from __future__ import annotations
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "backtest"))
from engine import Costs, RiskCfg, simulate, split_is_oos     # noqa: E402
from metrics import extended_metrics, passes_guardrails       # noqa: E402
from optimizer import optimize                                # noqa: E402

sys.path.insert(0, os.path.join(HERE, "..", "scalp_search_2026-05-30"))
import common as C                                            # noqa: E402
import strat_lib as SL                                        # noqa: E402
from donchian_family import donchian_breakout                 # noqa: E402

FAMILIES = dict(SL.REGISTRY)
FAMILIES["donchian_breakout"] = donchian_breakout

# momentum / breakout / reversal candidates + regime_mr benchmark
SPACES = {
    "donchian_breakout": dict(ema_slope_lb=3, atr_p=14,
        entry_n=("int", 10, 50), sl_atr=("float", 0.8, 2.5), tp_atr=("float", 2.0, 6.0),
        ema_len=("cat", [50, 100, 200]), vol_mult=("cat", [0.0, 1.0, 1.2, 1.5]),
        atr_min_pct=("float", 0.0, 0.8), trail_atr=("cat", [0.0, 2.0, 3.0, 4.0]),
        max_bars=("cat", [0, 48, 96])),
    "micro_pullback": dict(atr_p=14,
        impulse_atr=("float", 1.0, 2.5), pull_bars=("int", 1, 3), ema_len=("cat", [20, 50, 100]),
        sl_atr=("float", 0.8, 2.0), tp_atr=("float", 1.5, 4.0), max_bars=("int", 10, 40)),
    "vwap_reclaim": dict(atr_p=14, entry="market",
        sl_atr=("float", 1.0, 3.0), tp_atr=("float", 1.5, 5.0), buf_atr=("float", 0.0, 0.5),
        max_bars=("int", 12, 48)),
    "squeeze_expansion": dict(atr_p=14, entry="market",
        bb_len=("cat", [15, 20, 30]), bb_mult=("cat", [2.0, 2.5]), kc_mult=("cat", [1.0, 1.5]),
        sl_atr=("float", 1.0, 3.0), tp_atr=("float", 2.0, 6.0), min_squeeze=("int", 3, 12),
        max_bars=("cat", [24, 48, 96]), trail=("cat", [False, True])),
    "reclaim_pullback": dict(atr_p=14, entry="market",
        fast=("cat", [10, 20, 30]), slow=("cat", [50, 100, 200]), sl_atr=("float", 1.0, 3.0),
        tp_atr=("float", 2.0, 6.0), slope_lb=("cat", [5, 10, 20]), max_bars=("cat", [24, 48]),
        trail=("cat", [False, True])),
    "failed_breakout": dict(atr_p=14,
        lookback=("int", 10, 50), sl_atr=("float", 0.8, 2.0), tp_atr=("float", 1.5, 5.0),
        max_bars=("cat", [24, 48, 96])),
    "sweep_reversal": dict(atr_p=14,
        lookback=("int", 10, 50), sl_atr=("float", 0.8, 2.0), tp_atr=("float", 1.5, 5.0),
        max_bars=("cat", [24, 48, 96])),
    "regime_mr": dict(trend_len=200, slope_lb=20, limit_atr=0.25,
        z_period=("int", 20, 60), z_entry=("float", 1.0, 3.0), sl_atr=("float", 1.0, 3.0),
        tp_frac=("float", 0.2, 0.8), max_bars=("int", 6, 24)),
}

COINS = ["SOL", "ETH", "BTC", "HYPE", "ZEC"]
TF = "5m"
N_TRIALS = 60

LIGHTER = Costs(taker_pct=0.0, maker_pct=0.0, slippage_pct=0.05, funding_pct_per_8h=0.01)
BLOFIN = Costs(taker_pct=0.06, maker_pct=0.02, slippage_pct=0.05, funding_pct_per_8h=0.01)
RISK = RiskCfg(starting_equity=1000.0, risk_frac=0.01, max_leverage=20, liq_buffer=2.5, compounding=False)


def payoff_of(trades):
    w = [t.pnl_usd for t in trades if t.pnl_usd > 0]
    l = [-t.pnl_usd for t in trades if t.pnl_usd < 0]
    return (np.mean(w) / np.mean(l)) if (w and l) else float("inf")


def main():
    rows = []
    for fam, space in SPACES.items():
        fn = FAMILIES[fam]
        for coin in COINS:
            try:
                df = C.load(coin, TF)
            except Exception as e:
                print(f"  skip {coin}: {e}", file=sys.stderr); continue
            try:
                res = optimize(fn, df, space, costs=LIGHTER, risk=RISK, tf_minutes=C.TF_MIN[TF],
                               side="both", objective="calmar", n_trials=N_TRIALS)
            except Exception as e:
                print(f"  err {fam} {coin}: {e}", file=sys.stderr); continue
            # OOS payoff + BloFin robustness on the winner
            _, oos_df = split_is_oos(df, 0.70)
            oos_sigs = fn(oos_df, side="both", **res.best_params)
            oos_tr = simulate(oos_df, oos_sigs, LIGHTER, RISK, C.TF_MIN[TF])
            bf_tr = simulate(oos_df, oos_sigs, BLOFIN, RISK, C.TF_MIN[TF])
            bf_m = extended_metrics(bf_tr, RISK.starting_equity, compounding=False)
            om = res.oos_metrics
            rows.append(dict(
                family=fam, coin=coin, oos_pass=res.oos_pass,
                oos_n=om["n"], oos_pf=om["profit_factor"], oos_wr=om["win_rate"],
                oos_payoff=payoff_of(oos_tr), oos_net=om["net_pct"], oos_calmar=om["calmar"],
                oos_sharpe=om["sharpe"], oos_dd=om["max_dd_pct"], oos_liq=om["liq_hits"],
                bf_pf=bf_m["profit_factor"], bf_net=bf_m["net_pct"], bf_dd=bf_m["max_dd_pct"],
                is_pf=res.is_metrics["profit_factor"], is_net=res.is_metrics["net_pct"],
                params=res.best_params, reasons="; ".join(res.oos_fail_reasons),
            ))
            fin = lambda x: 999.0 if x == float("inf") else x
            print(f"  done {fam:<18}{coin:<5} OOS_PF={fin(om['profit_factor']):.2f} "
                  f"payoff={fin(payoff_of(oos_tr)):.2f} pass={res.oos_pass}", flush=True)

    dfr = pd.DataFrame(rows)
    out = os.path.join(HERE, "stage2_results.csv")
    dfr.to_csv(out, index=False)

    fin = lambda x: 999.0 if x == float("inf") else x
    dfr["calmar_s"] = dfr["oos_calmar"].map(fin)
    surv = dfr[dfr["oos_pass"]].sort_values("calmar_s", ascending=False)

    def pr(r):
        po = "inf" if r.oos_payoff == float("inf") else f"{r.oos_payoff:.2f}"
        cal = "inf" if r.oos_calmar == float("inf") else f"{r.oos_calmar:.1f}"
        print(f"  {r.family:<18}{r.coin:<5}OOSn={r.oos_n:>4} PF={fin(r.oos_pf):.2f} WR={r.oos_wr:>3.0f}% "
              f"payoff={po:>5} net={r.oos_net:>+6.1f}% Calmar={cal:>6} Sharpe={r.oos_sharpe:>5.2f} "
              f"DD={r.oos_dd:>4.1f}% | BloFin PF={fin(r.bf_pf):.2f} net={r.bf_net:>+6.1f}%")

    print(f"\n=== STAGE 2 ===  {len(dfr)} (family,coin) optimized.  "
          f"{len(surv)} cleared OOS guardrails (PF>=1.3, DD<=25%, Sharpe>=1, 0 liq).")
    print("\nOOS survivors (ranked by OOS Calmar) — payoff>1 = escaped the small-win/big-loss trap:")
    for _, r in surv.iterrows():
        pr(r)
    if surv.empty:
        print("  (none cleared — best near-misses:)")
        for _, r in dfr.sort_values("calmar_s", ascending=False).head(8).iterrows():
            pr(r); print(f"      failed: {r.reasons}")
    print(f"\nsaved: {out}")


if __name__ == "__main__":
    main()
