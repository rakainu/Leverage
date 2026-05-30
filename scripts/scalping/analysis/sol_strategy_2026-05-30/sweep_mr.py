"""IS/OOS sweep for the mean-reversion candidate (mr_fade) on SOL.

Discipline:
  - Split each timeframe 70/30 into in-sample (IS) / out-of-sample (OOS).
  - Evaluate every grid config on IS ONLY. Rank by IS profit factor, with a
    minimum trade-count guard so we don't reward 5-trade flukes.
  - Print the top IS configs WITH their OOS result side-by-side. A config only
    matters if it is profitable IS *and* holds up OOS.
  - All numbers are net of BloFin fees + slippage + funding.

Usage: python sweep_mr.py [5m|15m|1h]   (default runs all three)
"""
from __future__ import annotations
import sys, itertools, json, os
import pandas as pd
from btengine import load_sol, simulate, metrics, fmt, split_is_oos, Costs, RiskCfg
from strategies import mr_fade

COSTS = Costs()
RISK = RiskCfg(starting_equity=1000.0, risk_frac=0.01, max_leverage=30, liq_buffer=2.0, compounding=True)
TF_MIN = {"5m": 5, "15m": 15, "1h": 60}
MIN_TRADES_IS = 40   # IS guard (OOS is ~30% so fewer there)

# Grid — kept deliberately COARSE / low-dimensional to limit overfitting.
GRID = dict(
    z_period=[20, 30],
    z_entry=[2.0, 2.5, 3.0],
    sl_atr=[1.5, 2.0, 2.5],
    tp_frac=[0.7, 1.0],
    adx_max=[0, 25, 35],          # 0 = no regime filter
    rsi_os=[0, 30],               # paired with rsi_ob below
    max_bars=[24, 48],
    limit_atr=[0.0, 0.25],
)

def gen_configs():
    keys = list(GRID)
    for combo in itertools.product(*[GRID[k] for k in keys]):
        c = dict(zip(keys, combo))
        c["rsi_ob"] = 100 - c["rsi_os"] if c["rsi_os"] > 0 else 0  # symmetric RSI gate
        yield c

def run_tf(tf: str, top_n: int = 25):
    df = load_sol(tf)
    is_df, oos_df = split_is_oos(df, 0.70)
    print(f"\n{'#'*112}\n# SOL {tf}  IS={len(is_df)}b ({is_df.index[0].date()}->{is_df.index[-1].date()})  "
          f"OOS={len(oos_df)}b ({oos_df.index[0].date()}->{oos_df.index[-1].date()})\n{'#'*112}")
    rows = []
    for c in gen_configs():
        sigs = mr_fade(is_df, **c)
        tr = simulate(is_df, sigs, COSTS, RISK, TF_MIN[tf])
        m = metrics(tr, RISK.starting_equity)
        if m["n"] < MIN_TRADES_IS:
            continue
        rows.append((c, m))
    if not rows:
        print("  (no config cleared the IS trade-count guard)")
        return []
    # rank by IS profit factor, tie-break net pnl
    rows.sort(key=lambda r: (r[1]["profit_factor"], r[1]["net_pnl"]), reverse=True)

    results = []
    print(f"  Top {top_n} IS configs (ranked by IS PF) with OOS result:")
    for c, m_is in rows[:top_n]:
        sigs_o = mr_fade(oos_df, **c)
        tr_o = simulate(oos_df, sigs_o, COSTS, RISK, TF_MIN[tf])
        m_oos = metrics(tr_o, RISK.starting_equity)
        cs = ",".join(f"{k}={v}" for k, v in c.items() if k != "rsi_ob")
        print(f"   IS  {fmt(m_is)}")
        print(f"   OOS {fmt(m_oos)}   <- [{cs}]")
        results.append({"tf": tf, "config": c, "is": m_is, "oos": m_oos})
    return results

def main():
    tfs = [sys.argv[1]] if len(sys.argv) > 1 else ["1h", "15m", "5m"]
    allres = []
    for tf in tfs:
        allres.extend(run_tf(tf))
    out = os.path.join(os.path.dirname(__file__), "runs", "mr_sweep_results.json")
    with open(out, "w") as f:
        json.dump(allres, f, default=str, indent=1)
    print(f"\nsaved {len(allres)} ranked rows -> {out}")

if __name__ == "__main__":
    main()
