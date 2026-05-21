"""Batch 2 comparison: did exit-sweep lift the shared-best entry config?

Baseline: Batch 1 shared best (SL=$120, slope=0.12, body (0.3,0.6), noSun) with V3.1 default exits.
Compare each Batch 2 config (entry locked at shared best, exits swept) against the baseline.

Output:
  runs/batch2_exit_shared/COMPARISON_summary.csv  (all configs, both symbols, lift vs baseline)
  Top 10 printed to stdout.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from sweep import RUNS_DIR


def main():
    out_dir = RUNS_DIR / "batch2_exit_shared"
    zec_full = pd.read_csv(out_dir / "ZEC_batch2_exit_shared_full.csv")
    sol_full = pd.read_csv(out_dir / "SOL_batch2_exit_shared_full.csv")

    # Baseline: Batch 1 cross-symbol top-1 entry config with V3.1 default exits
    # (the exit defaults are baked into TrailParams, so we look for that exact match)
    # The shared-best entry was SL=$120 slope=0.12 body(0.3,0.6) noSun.
    # V3.1 default exits: BE=30, lock_act=45, lock=37.5, trail_act=75, trail_start=80, trail_dist=37.5

    # Easier: pick best Batch 2 config for each symbol, compare to Batch 1 reported best
    # (We loaded the cross-symbol summary earlier; the shared-best had ZEC net $20.8k, SOL net $6.4k)
    BASELINE_ZEC_LIGHTER = 20800.69
    BASELINE_SOL_LIGHTER = 6417.58
    BASELINE_ZEC_BLOFIN = 13000  # approx from B1
    BASELINE_SOL_BLOFIN = 2700   # approx from B1

    # Merge ZEC and SOL on the exit knobs (entry is fixed identical for all Batch 2)
    exit_keys = ["sl_loss_usdt", "breakeven_usdt", "lock_profit_activate_usdt",
                 "lock_profit_usdt", "trail_activate_usdt", "trail_start_usdt",
                 "trail_distance_usdt"]
    zec_keyed = zec_full.set_index(exit_keys)
    sol_keyed = sol_full.set_index(exit_keys)
    common = zec_keyed.index.intersection(sol_keyed.index)
    print(f"Shared exit configs in both: {len(common)}")

    rows = []
    for idx in common:
        z = zec_keyed.loc[idx]
        s = sol_keyed.loc[idx]
        if isinstance(z, pd.DataFrame):
            z = z.iloc[0]
        if isinstance(s, pd.DataFrame):
            s = s.iloc[0]
        d = {k: v for k, v in zip(exit_keys, idx)}
        d["zec_lighter_net"] = z["lighter_net_pnl"]
        d["zec_lighter_pf"] = z["lighter_profit_factor"]
        d["zec_lighter_dd"] = z["lighter_max_dd"]
        d["zec_lighter_n"] = z["lighter_n"]
        d["zec_blofin_net"] = z["blofin_net_pnl"]
        d["zec_blofin_pf"] = z["blofin_profit_factor"]

        d["sol_lighter_net"] = s["lighter_net_pnl"]
        d["sol_lighter_pf"] = s["lighter_profit_factor"]
        d["sol_lighter_dd"] = s["lighter_max_dd"]
        d["sol_lighter_n"] = s["lighter_n"]
        d["sol_blofin_net"] = s["blofin_net_pnl"]
        d["sol_blofin_pf"] = s["blofin_profit_factor"]

        d["combined_lighter_net"] = z["lighter_net_pnl"] + s["lighter_net_pnl"]
        d["combined_blofin_net"] = z["blofin_net_pnl"] + s["blofin_net_pnl"]
        d["both_profitable"] = bool(z["lighter_net_pnl"] > 0 and s["lighter_net_pnl"] > 0)

        # Lift vs Batch 1 shared-best baseline
        d["zec_lift"] = z["lighter_net_pnl"] - BASELINE_ZEC_LIGHTER
        d["sol_lift"] = s["lighter_net_pnl"] - BASELINE_SOL_LIGHTER
        d["combined_lift"] = d["zec_lift"] + d["sol_lift"]

        # Composite score: combined PnL, both profitable, low DD
        d["score"] = (
            (d["combined_lighter_net"] / 50000) * 50    # up to 50 pts for $50k combined
            + (z["lighter_profit_factor"] + s["lighter_profit_factor"]) / 6 * 20   # PF
            + (1 if d["both_profitable"] else 0) * 10
            + max(0, min(20, -10 * (z["lighter_max_dd"] + s["lighter_max_dd"]) / 1000))  # DD penalty
        )
        rows.append(d)

    df = pd.DataFrame(rows).sort_values("score", ascending=False)
    out_csv = out_dir / "COMPARISON_summary.csv"
    df.to_csv(out_csv, index=False)
    print(f"Wrote {out_csv}")

    # Top 10 by combined Lighter net
    print("\n" + "=" * 100)
    print(f"BASELINE (Batch 1 shared best, V3.1 default exits):")
    print(f"  ZEC Lighter: ${BASELINE_ZEC_LIGHTER:+,.0f}    SOL Lighter: ${BASELINE_SOL_LIGHTER:+,.0f}    "
          f"Combined: ${BASELINE_ZEC_LIGHTER + BASELINE_SOL_LIGHTER:+,.0f}")
    print("=" * 100)
    print("\nTOP 10 by composite score (Batch 2 exit configs):")
    print("-" * 100)
    cols = ["sl_loss_usdt", "breakeven_usdt", "lock_profit_activate_usdt",
            "trail_activate_usdt", "trail_distance_usdt",
            "zec_lighter_net", "zec_lighter_pf", "sol_lighter_net", "sol_lighter_pf",
            "combined_lighter_net", "combined_lift", "both_profitable"]
    print(df[cols].head(10).to_string(index=False))

    print("\nTOP 5 by raw combined Lighter PnL:")
    print("-" * 100)
    print(df.sort_values("combined_lighter_net", ascending=False)[cols].head(5).to_string(index=False))

    # Summary stats
    best = df.iloc[0]
    print("\n" + "=" * 100)
    print(f"BEST: SL=${best['sl_loss_usdt']:.0f}  BE=${best['breakeven_usdt']:.0f}  "
          f"lock_act=${best['lock_profit_activate_usdt']:.0f}  trail_act=${best['trail_activate_usdt']:.0f}  "
          f"trail_dist=${best['trail_distance_usdt']:.0f}")
    print(f"  ZEC: net=${best['zec_lighter_net']:+,.0f}  PF={best['zec_lighter_pf']:.2f}")
    print(f"  SOL: net=${best['sol_lighter_net']:+,.0f}  PF={best['sol_lighter_pf']:.2f}")
    print(f"  Combined Lighter: ${best['combined_lighter_net']:+,.0f}")
    print(f"  Lift vs Batch 1 baseline: ${best['combined_lift']:+,.0f}")


if __name__ == "__main__":
    main()
