"""Batch 2b comparison: did refining BE/trail_dist beat Batch 2 best?

Baseline: Batch 2 best (SL=$80, BE=$15, lock_act=$25, trail_act=$40, trail_dist=$15)
  ZEC Lighter: $30,300  SOL Lighter: $9,170  Combined: $39,470

Also surfaces:
  - Is BE=15 at the edge (i.e., do BE=8 or BE=10 do better) → overfit signal
  - Is trail_dist=15 robust across nearby values
"""
from __future__ import annotations
from pathlib import Path
import pandas as pd
from sweep import RUNS_DIR


BASELINE_ZEC = 30299.55
BASELINE_SOL = 9170.31
BASELINE_COMBINED = BASELINE_ZEC + BASELINE_SOL


def main():
    out_dir = RUNS_DIR / "batch2b_refinement"
    zec = pd.read_csv(out_dir / "ZEC_batch2b_refinement_full.csv")
    sol = pd.read_csv(out_dir / "SOL_batch2b_refinement_full.csv")

    exit_keys = ["sl_loss_usdt", "breakeven_usdt", "lock_profit_activate_usdt",
                 "lock_profit_usdt", "trail_activate_usdt", "trail_start_usdt",
                 "trail_distance_usdt"]
    zk = zec.set_index(exit_keys)
    sk = sol.set_index(exit_keys)
    common = zk.index.intersection(sk.index)
    print(f"Shared configs: {len(common)}")

    rows = []
    for idx in common:
        z = zk.loc[idx]; s = sk.loc[idx]
        if isinstance(z, pd.DataFrame): z = z.iloc[0]
        if isinstance(s, pd.DataFrame): s = s.iloc[0]
        d = dict(zip(exit_keys, idx))
        d["zec_net"] = z["lighter_net_pnl"]; d["zec_pf"] = z["lighter_profit_factor"]
        d["sol_net"] = s["lighter_net_pnl"]; d["sol_pf"] = s["lighter_profit_factor"]
        d["zec_dd"] = z["lighter_max_dd"]; d["sol_dd"] = s["lighter_max_dd"]
        d["combined_net"] = z["lighter_net_pnl"] + s["lighter_net_pnl"]
        d["lift_vs_b2"] = d["combined_net"] - BASELINE_COMBINED
        d["both_profitable"] = bool(z["lighter_net_pnl"] > 0 and s["lighter_net_pnl"] > 0)
        rows.append(d)

    df = pd.DataFrame(rows).sort_values("combined_net", ascending=False)
    out_csv = out_dir / "COMPARISON_summary.csv"
    df.to_csv(out_csv, index=False)
    print(f"Wrote {out_csv}")

    print("\n" + "=" * 100)
    print(f"BASELINE (Batch 2 best): ZEC=${BASELINE_ZEC:+,.0f}  SOL=${BASELINE_SOL:+,.0f}  Combined=${BASELINE_COMBINED:+,.0f}")
    print("=" * 100)

    cols = ["sl_loss_usdt", "breakeven_usdt", "trail_activate_usdt", "trail_distance_usdt",
            "zec_net", "zec_pf", "sol_net", "sol_pf", "combined_net", "lift_vs_b2", "both_profitable"]
    print("\nTOP 10 by combined Lighter net PnL:")
    print(df[cols].head(10).to_string(index=False))

    print("\n" + "=" * 100)
    print("BE OPTIMUM CHECK — is BE=15 an interior optimum or at the edge?")
    print("(For each BE value, show the best combined PnL over all other knobs)")
    print("=" * 100)
    be_best = df.groupby("breakeven_usdt").agg(
        best_combined=("combined_net", "max"),
        mean_combined=("combined_net", "mean"),
        n_configs=("combined_net", "size"),
        worst_combined=("combined_net", "min"),
    ).reset_index().sort_values("breakeven_usdt")
    print(be_best.to_string(index=False))

    print("\nTRAIL_DIST OPTIMUM CHECK — is trail_dist=15 interior?")
    td_best = df.groupby("trail_distance_usdt").agg(
        best_combined=("combined_net", "max"),
        mean_combined=("combined_net", "mean"),
        n_configs=("combined_net", "size"),
    ).reset_index().sort_values("trail_distance_usdt")
    print(td_best.to_string(index=False))

    # Lift summary
    pos_lift = df[df["lift_vs_b2"] > 0]
    print(f"\nConfigs that LIFT vs Batch 2 baseline: {len(pos_lift)}/{len(df)}")
    if not pos_lift.empty:
        max_lift = pos_lift["lift_vs_b2"].max()
        print(f"Max lift: ${max_lift:+,.0f}")
        print("\nBest single config:")
        best = df.iloc[0]
        print(f"  SL=${best['sl_loss_usdt']:.0f} BE=${best['breakeven_usdt']:.0f} "
              f"lock_act=${best['lock_profit_activate_usdt']:.0f} "
              f"trail_act=${best['trail_activate_usdt']:.0f} "
              f"trail_dist=${best['trail_distance_usdt']:.0f}")
        print(f"  ZEC ${best['zec_net']:+,.0f} (PF {best['zec_pf']:.2f})  "
              f"SOL ${best['sol_net']:+,.0f} (PF {best['sol_pf']:.2f})  "
              f"Combined ${best['combined_net']:+,.0f}")


if __name__ == "__main__":
    main()
