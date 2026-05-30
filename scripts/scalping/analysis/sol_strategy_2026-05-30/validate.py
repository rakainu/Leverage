"""Rigorous validation of the SOL 1h mean-reversion candidate.

For a shortlist of configs from the robust IS neighborhood, report:
  - FULL-period metrics (the headline "tested period" numbers)
  - IS (70%) and OOS (30%) split
  - 4-fold walk-forward: train PF + test PF per fold (sign-consistency check)

Selection rule: pick on robustness (positive across folds + OOS), NOT on the single
best OOS number. All net of BloFin fees + slippage + funding.
"""
from __future__ import annotations
import json, os
from btengine import load_sol, simulate, metrics, fmt, split_is_oos, walk_forward_folds, Costs, RiskCfg
from strategies import mr_fade

COSTS = Costs()
RISK = RiskCfg(starting_equity=1000.0, risk_frac=0.01, max_leverage=30, liq_buffer=2.0, compounding=True)
TF = "1h"; TF_MIN = 60

# Robust neighborhood shortlist (all share z=20, ze=2.5, adx_max=35, tp_frac=1.0)
SHORTLIST = [
    dict(z_period=20, z_entry=2.5, sl_atr=2.0, tp_frac=1.0, adx_max=35, max_bars=48, limit_atr=0.0),
    dict(z_period=20, z_entry=2.5, sl_atr=2.0, tp_frac=1.0, adx_max=35, max_bars=48, limit_atr=0.25),
    dict(z_period=20, z_entry=2.5, sl_atr=2.5, tp_frac=1.0, adx_max=35, max_bars=24, limit_atr=0.25),
    dict(z_period=20, z_entry=2.5, sl_atr=2.5, tp_frac=1.0, adx_max=35, max_bars=48, limit_atr=0.25),
    dict(z_period=20, z_entry=2.0, sl_atr=2.0, tp_frac=1.0, adx_max=35, max_bars=48, limit_atr=0.0),
]

def evalcfg(df, c):
    return metrics(simulate(df, mr_fade(df, **c), COSTS, RISK, TF_MIN), RISK.starting_equity)

def main():
    df = load_sol(TF)
    is_df, oos_df = split_is_oos(df, 0.70)
    folds = walk_forward_folds(df, 4)
    out = []
    for c in SHORTLIST:
        cs = ",".join(f"{k}={v}" for k, v in c.items())
        m_full = evalcfg(df, c); m_is = evalcfg(is_df, c); m_oos = evalcfg(oos_df, c)
        print(f"\n=== [{cs}]")
        print(f"  FULL {fmt(m_full)}")
        print(f"  IS   {fmt(m_is)}")
        print(f"  OOS  {fmt(m_oos)}")
        fold_line = []
        wf_test_pf = []
        for k, (tr_df, te_df) in enumerate(folds):
            mtr = evalcfg(tr_df, c); mte = evalcfg(te_df, c)
            wf_test_pf.append(mte["profit_factor"])
            fold_line.append(f"f{k}:tr PF{mtr['profit_factor']:.2f}/te PF{mte['profit_factor']:.2f}(n{mte['n']},{mte['net_pct']:+.0f}%)")
        print("  WF   " + "  ".join(fold_line))
        pos_folds = sum(1 for p in wf_test_pf if p > 1.0)
        out.append({"config": c, "full": m_full, "is": m_is, "oos": m_oos,
                    "wf_test_pf": wf_test_pf, "pos_test_folds": pos_folds})
    p = os.path.join(os.path.dirname(__file__), "runs", "validate_1h.json")
    with open(p, "w") as f:
        json.dump(out, f, default=str, indent=1)
    print(f"\nsaved -> {p}")

if __name__ == "__main__":
    main()
