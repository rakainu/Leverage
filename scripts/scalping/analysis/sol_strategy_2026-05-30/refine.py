"""Principled refinement of the SOL 1h MR base config (A).

Hypothesis: over a strong uptrend (SOL 67->148), MR LONGS (buy dips) carry the
edge while SHORTS (fade rips) bleed. Test side asymmetry + trend alignment +
regime tightness on a SMALL, interpretable grid. Tune on IS, confirm OOS + WF.

Base: z_period=20, z_entry=2.5, tp_frac=1.0, max_bars=48, limit_atr=0.0, sl_atr=2.0
Vary: side_only {0,+1,-1}, trend_filter {0,+1}, adx_max {25,30,35}
"""
from __future__ import annotations
import itertools
from btengine import load_sol, simulate, metrics, fmt, split_is_oos, walk_forward_folds, Costs, RiskCfg
from strategies import mr_fade

COSTS = Costs()
RISK = RiskCfg(starting_equity=1000.0, risk_frac=0.01, max_leverage=30, liq_buffer=2.0, compounding=True)
TF_MIN = 60
BASE = dict(z_period=20, z_entry=2.5, sl_atr=2.0, tp_frac=1.0, max_bars=48, limit_atr=0.0)

def evalcfg(df, c):
    return metrics(simulate(df, mr_fade(df, **c), COSTS, RISK, TF_MIN), RISK.starting_equity)

def main():
    df = load_sol("1h"); is_df, oos_df = split_is_oos(df, 0.70); folds = walk_forward_folds(df, 4)
    rows = []
    for side_only, trend_filter, adx_max in itertools.product([0, 1, -1], [0, 1], [25, 30, 35]):
        c = {**BASE, "side_only": side_only, "trend_filter": trend_filter, "adx_max": adx_max}
        m_is = evalcfg(is_df, c)
        if m_is["n"] < 30:
            continue
        m_full, m_oos = evalcfg(df, c), evalcfg(oos_df, c)
        wf = [evalcfg(te, c)["profit_factor"] for _, te in folds]
        pos = sum(1 for p in wf if p > 1.0)
        rows.append((c, m_is, m_oos, m_full, wf, pos))
    rows.sort(key=lambda r: r[3]["profit_factor"], reverse=True)  # rank by FULL PF
    for c, m_is, m_oos, m_full, wf, pos in rows:
        tag = f"side={c['side_only']:+d} trend={c['trend_filter']} adx<={c['adx_max']}"
        print(f"\n{tag}")
        print(f"  FULL {fmt(m_full)}")
        print(f"  IS   {fmt(m_is)}")
        print(f"  OOS  {fmt(m_oos)}")
        print(f"  WF   test PFs {[round(p,2) for p in wf]}  ({pos}/4 positive)")

if __name__ == "__main__":
    main()
