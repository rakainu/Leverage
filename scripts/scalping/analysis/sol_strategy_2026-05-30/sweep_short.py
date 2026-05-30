"""Push the SOL 1h SHORT-ONLY mean-reversion edge toward >=80 trades while holding
PF>=1.30. Same structural hypothesis (fade overbought rips in non-strong-trend
regimes); explore entry threshold + regime width + stop. Acceptance gate stays
strict: must hold OOS and across walk-forward folds, not just IS.
"""
from __future__ import annotations
import itertools, json, os
from btengine import load_sol, simulate, metrics, fmt, split_is_oos, walk_forward_folds, Costs, RiskCfg
from strategies import mr_fade

COSTS = Costs()
RISK = RiskCfg(starting_equity=1000.0, risk_frac=0.01, max_leverage=30, liq_buffer=2.0, compounding=True)
TF_MIN = 60

def evalcfg(df, c):
    return metrics(simulate(df, mr_fade(df, **c), COSTS, RISK, TF_MIN), RISK.starting_equity)

def main():
    df = load_sol("1h"); is_df, oos_df = split_is_oos(df, 0.70); folds = walk_forward_folds(df, 4)
    rows = []
    for z_entry, adx_max, sl_atr, la, mb in itertools.product(
            [2.0, 2.25, 2.5], [35, 40, 45, 50], [2.0, 2.5], [0.0, 0.25], [48, 72]):
        c = dict(z_period=20, z_entry=z_entry, sl_atr=sl_atr, tp_frac=1.0, adx_max=adx_max,
                 max_bars=mb, limit_atr=la, side_only=-1)
        m_full = evalcfg(df, c)
        m_is = evalcfg(is_df, c); m_oos = evalcfg(oos_df, c)
        wf = [evalcfg(te, c)["profit_factor"] for _, te in folds]
        pos = sum(1 for p in wf if p > 1.0)
        rows.append((c, m_is, m_oos, m_full, wf, pos))
    # candidates that satisfy ALL strict criteria on the FULL period + positive OOS
    def passes(m_full, m_oos):
        return (m_full["profit_factor"] >= 1.30 and m_full["n"] >= 80
                and m_full["max_dd_pct"] < 20 and m_oos["net_pnl"] > 0)
    rows.sort(key=lambda r: (passes(r[3], r[2]), r[3]["n"] >= 80, r[3]["profit_factor"]), reverse=True)
    print("Ranked (full-criteria passers first, then >=80 trades, then PF):\n")
    for c, m_is, m_oos, m_full, wf, pos in rows[:18]:
        flag = "PASS-ALL" if passes(m_full, m_oos) else ("n>=80" if m_full["n"] >= 80 else "")
        print(f"ze={c['z_entry']} adx<={c['adx_max']} sl={c['sl_atr']} la={c['limit_atr']} mb={c['max_bars']}  {flag}")
        print(f"  FULL {fmt(m_full)}")
        print(f"  OOS  {fmt(m_oos)}   WF {[round(p,2) for p in wf]} ({pos}/4)")
    out = [{"config": c, "is": mi, "oos": mo, "full": mf, "wf": wf, "pos": p}
           for c, mi, mo, mf, wf, p in rows]
    with open(os.path.join(os.path.dirname(__file__), "runs", "short_sweep.json"), "w") as f:
        json.dump(out, f, default=str, indent=1)

if __name__ == "__main__":
    main()
