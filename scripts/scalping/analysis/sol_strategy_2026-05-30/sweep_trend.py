"""Proper IS/OOS sweep of TREND-FOLLOWING on SOL 1h (Donchian breakout + ADX gate,
ATR trail or R-mult TP). MR was the only family swept rigorously; this gives trend
the same fair shot before any final verdict. Best IS config is cross-checked OOS
and across instruments (BTC/ETH) to test for a generalizable edge.
"""
from __future__ import annotations
import itertools, numpy as np
from btengine import load_symbol_tf, simulate, metrics, fmt, split_is_oos, Costs, RiskCfg
from strategies import donchian_breakout, adx_breakout

COSTS = Costs(); RISK = RiskCfg(max_leverage=20, liq_buffer=2.5); TF = 60
MIN_IS = 30

def sweep(fam, df_is, df_oos):
    grids = {
        "donchian": dict(channel=[20, 30, 50], sl_atr=[1.5, 2.0, 3.0], tp_atr=[2.0, 3.0, 4.0],
                         trail=[False, True], min_atr_pct=[0.0, 0.5]),
        "adx_breakout": dict(channel=[20, 30, 50], adx_min=[18, 22, 28], sl_atr=[1.5, 2.0, 3.0],
                             tp_atr=[2.0, 3.0, 4.0], trail=[False, True]),
    }
    fn = {"donchian": donchian_breakout, "adx_breakout": adx_breakout}[fam]
    g = grids[fam]; keys = list(g)
    rows = []
    for combo in itertools.product(*[g[k] for k in keys]):
        c = dict(zip(keys, combo))
        m_is = metrics(simulate(df_is, fn(df_is, **c), COSTS, RISK, TF), 1000.0)
        if m_is["n"] < MIN_IS:
            continue
        m_oos = metrics(simulate(df_oos, fn(df_oos, **c), COSTS, RISK, TF), 1000.0)
        rows.append((c, m_is, m_oos))
    rows.sort(key=lambda r: r[1]["profit_factor"], reverse=True)
    return rows

def main():
    df = load_symbol_tf("SOL", "1h"); is_df, oos_df = split_is_oos(df, 0.70)
    for fam in ["donchian", "adx_breakout"]:
        rows = sweep(fam, is_df, oos_df)
        print(f"\n{'='*100}\n{fam}  top 8 IS configs (SOL 1h) with OOS:\n{'='*100}")
        for c, mi, mo in rows[:8]:
            cs = ",".join(f"{k}={v}" for k, v in c.items())
            print(f"  IS  {fmt(mi)}")
            print(f"  OOS {fmt(mo)}  <- [{cs}]")
        # cross-instrument check on the IS-best
        if rows:
            cbest = rows[0][0]
            fn = {"donchian": donchian_breakout, "adx_breakout": adx_breakout}[fam]
            print(f"  cross-instrument (IS-best {fam}):")
            for sym in ["SOL", "BTC", "ETH"]:
                d = load_symbol_tf(sym, "1h")
                mm = metrics(simulate(d, fn(d, **cbest), COSTS, RISK, TF), 1000.0)
                print(f"     {sym}: {fmt(mm)}")

if __name__ == "__main__":
    main()
