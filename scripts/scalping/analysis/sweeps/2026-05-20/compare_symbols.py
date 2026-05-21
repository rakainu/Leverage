"""Cross-symbol shared-setting analyzer.

For each config that exists in BOTH symbol sweeps, compute:
  - Combined score (avg of per-symbol Lighter scores)
  - Symbol-divergence (max-min of per-symbol net PnL)
  - Cross-symbol consistency flag

Output:
  runs/<batch_pair>/cross_symbol_summary.csv
  Best 10 shared configs printed to stdout.

Usage:
  python compare_symbols.py --batch1 batch1_zec_anchor --batch2 batch3_sol_anchor
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from sweep import RUNS_DIR


def load_batch(batch_name: str, symbol: str) -> pd.DataFrame:
    p = RUNS_DIR / batch_name / f"{symbol}_{batch_name}_full.csv"
    if not p.exists():
        raise SystemExit(f"missing {p}")
    df = pd.read_csv(p)
    df["__symbol"] = symbol
    df["__batch"] = batch_name
    return df


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch1", required=True, help="ZEC batch name")
    ap.add_argument("--batch2", required=True, help="SOL batch name")
    args = ap.parse_args()

    zec = load_batch(args.batch1, "ZEC")
    sol = load_batch(args.batch2, "SOL")

    # Identify shared knob columns
    key_cols = ["sl_loss_usdt", "min_abs_slope_pct", "block_body_band", "block_sunday"]
    key_cols = [c for c in key_cols if c in zec.columns and c in sol.columns]

    zec_keyed = zec.set_index(key_cols)
    sol_keyed = sol.set_index(key_cols)

    common_idx = zec_keyed.index.intersection(sol_keyed.index)
    print(f"Shared configs: {len(common_idx)}")

    rows = []
    for idx in common_idx:
        z = zec_keyed.loc[idx]
        s = sol_keyed.loc[idx]
        # Handle case where pivot table returns multiple rows
        if isinstance(z, pd.DataFrame):
            z = z.iloc[0]
        if isinstance(s, pd.DataFrame):
            s = s.iloc[0]
        row = {k: v for k, v in zip(key_cols, idx)}
        # Lighter (target) metrics
        for prefix, sym, src in [("zec_", "ZEC", z), ("sol_", "SOL", s)]:
            row[prefix + "score"] = src.get("score_lighter", 0)
            row[prefix + "net_pnl"] = src.get("lighter_net_pnl", 0)
            row[prefix + "pf"] = src.get("lighter_profit_factor", 0)
            row[prefix + "n"] = src.get("lighter_n", 0)
            row[prefix + "dd"] = src.get("lighter_max_dd", 0)
            row[prefix + "wr"] = src.get("lighter_win_rate", 0)
        # Combined / consistency
        row["combined_score"] = (row["zec_score"] + row["sol_score"]) / 2
        row["combined_net"] = row["zec_net_pnl"] + row["sol_net_pnl"]
        row["both_profitable"] = bool(row["zec_net_pnl"] > 0 and row["sol_net_pnl"] > 0)
        row["pnl_spread"] = abs(row["zec_net_pnl"] - row["sol_net_pnl"])
        rows.append(row)

    df = pd.DataFrame(rows).sort_values("combined_score", ascending=False)

    out_dir = RUNS_DIR / f"cross_{args.batch1}_vs_{args.batch2}"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_csv = out_dir / "cross_symbol_summary.csv"
    df.to_csv(out_csv, index=False)
    print(f"Wrote {out_csv}")

    # Best shared configs (both profitable, ranked by combined score)
    print("\n" + "=" * 80)
    print("TOP 10 SHARED CONFIGS (both ZEC and SOL profitable)")
    print("=" * 80)
    both_ok = df[df["both_profitable"]].head(10)
    if both_ok.empty:
        print("(no config profitable on BOTH symbols)")
    else:
        cols_show = key_cols + ["combined_score", "combined_net",
                                "zec_net_pnl", "zec_pf", "sol_net_pnl", "sol_pf"]
        print(both_ok[cols_show].to_string(index=False))

    print("\n" + "=" * 80)
    print("TOP 10 BY COMBINED SCORE (regardless of cross-symbol)")
    print("=" * 80)
    cols_show = key_cols + ["combined_score", "combined_net",
                            "zec_net_pnl", "zec_pf", "sol_net_pnl", "sol_pf"]
    print(df[cols_show].head(10).to_string(index=False))


if __name__ == "__main__":
    main()
