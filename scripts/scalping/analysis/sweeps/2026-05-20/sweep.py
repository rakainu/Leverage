"""Grid sweep harness — dual-pass (BloFin + Lighter) scoring.

For each parameter combination:
  - Run the backtest twice (BloFin fees, Lighter fees)
  - Score on a 0-100 scale (profitability / DD control / robustness / etc.)
  - Walk-forward split: in-sample (first 70%) vs out-of-sample (last 30%)

Output:
  runs/<batch_name>/<symbol>_<batch>_full.csv      every config, full metrics
  runs/<batch_name>/<symbol>_<batch>_summary.csv   top-50 sorted by Lighter score
  runs/<batch_name>/manifest.json                  grid spec + run metadata

Usage:
  python sweep.py --batch batch1_zec_anchor --symbol ZEC --days 180
"""
from __future__ import annotations

import argparse
import csv
import itertools
import json
import time
from dataclasses import asdict, replace
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

from engine import load_symbol
from strategy import (
    TrailParams, EntryFilters, prepare_dataframe, run_backtest, kpis,
)

RUNS_DIR = Path(__file__).resolve().parent / "runs"
RUNS_DIR.mkdir(exist_ok=True)


# ---------- Fee profiles ----------

FEE_PROFILES = {
    "blofin":  {"commission_pct": 0.0006},  # 0.06% per side, taker
    "lighter": {"commission_pct": 0.0000},  # zero fees
}


# ---------- Grid generators ----------

def grid_batch1_zec_anchor() -> list[dict]:
    """Batch 1: anchor expansion around the F8 winner (slope=0.15 + noSun + body 0.3-0.5 + SL=$82.50).

    Sweeps SL, slope gate, body-band on/off, Sunday-block on/off. ~360 configs.
    Other exit knobs (BE/lock/trail) held at V3.1 live defaults — sweep them separately.
    """
    configs = []
    for sl in [60, 65, 70, 75, 80, 82.5, 85, 90, 95, 100, 110, 120]:
        for slope in [0.09, 0.12, 0.15, 0.18, 0.21]:
            for body_band in [None, (0.3, 0.5), (0.2, 0.5), (0.3, 0.6)]:
                for block_sun in [True, False]:
                    configs.append({
                        "sl_loss_usdt": sl,
                        "min_abs_slope_pct": slope,
                        "block_body_band": body_band,
                        "block_sunday": block_sun,
                    })
    return configs


def grid_batch2_zec_full() -> list[dict]:
    """Batch 2: wider ZEC sweep — adds BE/lock/trail variants on top of Batch 1 best region."""
    configs = []
    for sl in [50, 70, 82.5, 95, 110]:
        for slope in [0.09, 0.15, 0.21]:
            for body_band in [None, (0.3, 0.5)]:
                for be in [20, 30, 40]:
                    for lock_act in [40, 55, 70]:
                        if lock_act <= be:
                            continue
                        for trail_act in [60, 80, 100]:
                            if trail_act <= lock_act:
                                continue
                            configs.append({
                                "sl_loss_usdt": sl,
                                "min_abs_slope_pct": slope,
                                "block_body_band": body_band,
                                "block_sunday": True,
                                "breakeven_usdt": be,
                                "lock_profit_activate_usdt": lock_act,
                                "lock_profit_usdt": be,  # lock = BE distance, conservative
                                "trail_activate_usdt": trail_act,
                                "trail_start_usdt": trail_act + 5,
                                "trail_distance_usdt": be * 1.25,
                            })
    return configs


def grid_batch3_sol_anchor() -> list[dict]:
    """Batch 3: same surface as Batch 1, applied to SOL. SOL was historically borderline."""
    return grid_batch1_zec_anchor()


def grid_batch2_exit_shared() -> list[dict]:
    """Batch 2: exit state-machine sweep on top of the shared-best entry config.

    Entry locked at: SL=$120, slope=0.12, body (0.3, 0.6), block Sunday.
    Sweeps BE, lock activation/amount, trail activation/start/distance.
    After pruning illegal combos (lock < BE, trail < lock_act): ~100 configs.

    SL is also varied around the shared best ($120) to allow BE/SL interaction.
    """
    # Fixed entry (shared best from Batch 1+3 cross-symbol):
    entry_fixed = {
        "min_abs_slope_pct": 0.12,
        "block_body_band": (0.3, 0.6),
        "block_sunday": True,
    }

    configs = []
    # Trim to dominant knobs: SL, BE, lock_offset, trail_offset, trail_dist multiplier.
    # Fix lock_amt = BE+5 (small-effect axis) and trail_start = trail_act+5.
    for sl in [80, 100, 120, 140]:                      # widen around shared best
        for be in [15, 25, 35, 45]:                     # breakeven trigger ($)
            for lock_offset in [10, 20, 30]:            # lock_act = BE + offset
                lock_act = be + lock_offset
                lock_amt = be + 5
                for trail_offset in [15, 30]:           # trail_act = lock_act + offset
                    trail_act = lock_act + trail_offset
                    trail_start = trail_act + 5
                    for trail_dist_mult in [1.0, 1.5]:  # multiplier on BE
                        trail_dist = round(be * trail_dist_mult, 2)
                        if lock_amt <= 0 or lock_amt >= sl:
                            continue
                        configs.append({
                            **entry_fixed,
                            "sl_loss_usdt": sl,
                            "breakeven_usdt": be,
                            "lock_profit_activate_usdt": lock_act,
                            "lock_profit_usdt": lock_amt,
                            "trail_activate_usdt": trail_act,
                            "trail_start_usdt": trail_start,
                            "trail_distance_usdt": trail_dist,
                        })
    return configs


# ---------- Runners ----------

def cfg_to_params(cfg: dict) -> tuple[TrailParams, EntryFilters]:
    """Translate a flat config dict into (TrailParams, EntryFilters)."""
    p = TrailParams()
    p_fields = {f for f in p.__dataclass_fields__}
    p_kwargs = {k: v for k, v in cfg.items() if k in p_fields}
    p = replace(p, **p_kwargs)

    f = EntryFilters()
    if cfg.get("block_sunday"):
        f.block_weekdays = {6}
    if cfg.get("min_abs_slope_pct"):
        f.min_abs_slope_pct = cfg["min_abs_slope_pct"]
    if cfg.get("block_body_band"):
        f.block_body_band = cfg["block_body_band"]
    if cfg.get("min_body_atr_ratio"):
        f.min_body_atr_ratio = cfg["min_body_atr_ratio"]
    if cfg.get("max_body_atr_ratio"):
        f.max_body_atr_ratio = cfg["max_body_atr_ratio"]
    if cfg.get("block_hours_utc"):
        f.block_hours_utc = set(cfg["block_hours_utc"])
    if cfg.get("min_adx"):
        f.min_adx = cfg["min_adx"]
    if cfg.get("max_adx"):
        f.max_adx = cfg["max_adx"]
    return p, f


def split_oos(tdf: pd.DataFrame, oos_frac: float = 0.30) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Chronological train/OOS split. Returns (in_sample, out_of_sample)."""
    if tdf.empty:
        return tdf, tdf
    cut = int(len(tdf) * (1 - oos_frac))
    return tdf.iloc[:cut], tdf.iloc[cut:]


def run_one(df: pd.DataFrame, cfg: dict) -> dict:
    """Run a single config across both fee profiles, return flat KPI dict."""
    p_base, filt = cfg_to_params(cfg)

    out: dict = {**cfg}
    out["block_body_band"] = str(cfg.get("block_body_band"))   # CSV-friendly
    for fee_name, fee in FEE_PROFILES.items():
        p = replace(p_base, commission_pct=fee["commission_pct"])
        _, tdf = run_backtest(df, p, filters=filt)
        full = kpis(tdf)
        is_part, oos_part = split_oos(tdf)
        is_k = kpis(is_part)
        oos_k = kpis(oos_part)
        prefix = fee_name + "_"
        for k, v in full.items():
            out[prefix + k] = v
        out[prefix + "is_net_pnl"] = is_k["net_pnl"]
        out[prefix + "is_pf"] = is_k["profit_factor"]
        out[prefix + "oos_net_pnl"] = oos_k["net_pnl"]
        out[prefix + "oos_pf"] = oos_k["profit_factor"]
        out[prefix + "oos_n"] = oos_k["n"]
    return out


# ---------- Scoring ----------

def score_row(r: dict, fee_prefix: str = "lighter_") -> float:
    """Composite 0-100 score for one row under a fee profile."""
    net = r.get(fee_prefix + "net_pnl", 0)
    pf = r.get(fee_prefix + "profit_factor", 0)
    dd = abs(r.get(fee_prefix + "max_dd", 0))
    n = r.get(fee_prefix + "n", 0)
    wr = r.get(fee_prefix + "win_rate", 0)
    oos_pnl = r.get(fee_prefix + "oos_net_pnl", 0)

    # Profitability: clip at +$5,000 net for 100 pts
    s_prof = max(0, min(net / 5000, 1.0)) * 25
    # PF: 2.0 = 20 pts, 1.0 = 0 pts
    s_pf = max(0, min((pf - 1.0) / 1.0, 1.0)) * 20
    # Drawdown control: smaller DD relative to net = better. dd/net ratio.
    if net > 0:
        ratio = dd / net  # smaller = better
        s_dd = max(0, min((1 - ratio / 2.0), 1.0)) * 20
    else:
        s_dd = 0
    # Trade count reliability: 100+ trades = full credit
    s_n = max(0, min(n / 100, 1.0)) * 15
    # OOS survival: profitable OOS = full credit, breakeven = half
    if net > 0:
        oos_ratio = oos_pnl / max(net * 0.3, 1)  # OOS should be ~30% of total
        s_oos = max(0, min(oos_ratio, 1.0)) * 20
    else:
        s_oos = 0

    return round(s_prof + s_pf + s_dd + s_n + s_oos, 2)


# ---------- Main ----------

def grid_batch2b_refinement() -> list[dict]:
    """Batch 2b refinement: explore tighter BE + decouple trail_dist from BE.

    Tests whether BE=15 was at the edge of an overfit zone (would suggest going
    even tighter gives even better numbers — a red flag) or whether it's a true
    interior optimum.

    Entry locked at shared best. Holds lock structure relative to BE.
    """
    entry_fixed = {
        "min_abs_slope_pct": 0.12,
        "block_body_band": (0.3, 0.6),
        "block_sunday": True,
    }
    configs = []
    for sl in [80, 100, 120]:
        for be in [8, 10, 12, 15, 18, 22]:           # 6 values — go tighter + looser than 15
            lock_act = be + 10
            lock_amt = be + 5
            for trail_act_offset in [10, 15, 20]:    # lock_act + offset = trail_act
                trail_act = lock_act + trail_act_offset
                trail_start = trail_act + 5
                for trail_dist in [10, 15, 20]:      # absolute $ — decoupled from BE
                    if lock_amt >= sl:
                        continue
                    configs.append({
                        **entry_fixed,
                        "sl_loss_usdt": sl,
                        "breakeven_usdt": be,
                        "lock_profit_activate_usdt": lock_act,
                        "lock_profit_usdt": lock_amt,
                        "trail_activate_usdt": trail_act,
                        "trail_start_usdt": trail_start,
                        "trail_distance_usdt": trail_dist,
                    })
    return configs


GRIDS = {
    "batch1_zec_anchor": grid_batch1_zec_anchor,
    "batch2_zec_full": grid_batch2_zec_full,
    "batch3_sol_anchor": grid_batch3_sol_anchor,
    "batch2_exit_shared": grid_batch2_exit_shared,
    "batch2b_refinement": grid_batch2b_refinement,
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", required=True, choices=list(GRIDS.keys()))
    ap.add_argument("--symbol", required=True, choices=["SOL", "ZEC"])
    ap.add_argument("--days", type=int, default=180)
    ap.add_argument("--timeframe", default="5m")
    args = ap.parse_args()

    print("=" * 72)
    print(f"SWEEP — {args.batch}  symbol={args.symbol}  days={args.days}")
    print("=" * 72)

    # Load + prep data (cached)
    t0 = time.time()
    df = load_symbol(args.symbol, args.timeframe, days_back=args.days)
    df = prepare_dataframe(df)
    print(f"\nBars: {len(df)}  range: {df.index[0]} -> {df.index[-1]}")
    print(f"Pine sigs: buy={int(df['buy_sig'].sum())}  sell={int(df['sell_sig'].sum())}")
    print(f"data prep: {time.time() - t0:.1f}s")

    configs = GRIDS[args.batch]()
    print(f"\nGrid size: {len(configs)} configs  (each runs 2 fee profiles)")

    t0 = time.time()
    rows = []
    for i, cfg in enumerate(configs, 1):
        if i % 20 == 0 or i == 1:
            elapsed = time.time() - t0
            eta = (elapsed / i) * (len(configs) - i)
            print(f"  [{i:>4}/{len(configs)}] elapsed={elapsed:.0f}s eta={eta:.0f}s")
        row = run_one(df, cfg)
        row["score_lighter"] = score_row(row, "lighter_")
        row["score_blofin"] = score_row(row, "blofin_")
        rows.append(row)

    total = time.time() - t0
    print(f"\nSweep complete: {len(rows)} configs in {total:.1f}s ({total/len(rows):.2f}s/cfg)")

    # Write output
    out_dir = RUNS_DIR / args.batch
    out_dir.mkdir(parents=True, exist_ok=True)
    full_csv = out_dir / f"{args.symbol}_{args.batch}_full.csv"
    df_out = pd.DataFrame(rows)
    df_out.to_csv(full_csv, index=False)
    print(f"\nWrote {full_csv}")

    # Top-50 summary by Lighter score
    summary_csv = out_dir / f"{args.symbol}_{args.batch}_summary.csv"
    cols_top = [
        "score_lighter", "score_blofin",
        "sl_loss_usdt", "min_abs_slope_pct", "block_body_band", "block_sunday",
        "lighter_n", "lighter_net_pnl", "lighter_profit_factor", "lighter_max_dd",
        "lighter_win_rate", "lighter_oos_net_pnl", "lighter_oos_pf",
        "blofin_net_pnl", "blofin_profit_factor", "blofin_max_dd",
    ]
    cols_top = [c for c in cols_top if c in df_out.columns]
    top = df_out.sort_values("score_lighter", ascending=False)[cols_top].head(50)
    top.to_csv(summary_csv, index=False)
    print(f"Wrote {summary_csv}")

    # Manifest
    manifest = {
        "batch": args.batch,
        "symbol": args.symbol,
        "timeframe": args.timeframe,
        "days_back": args.days,
        "bars": len(df),
        "data_window": [str(df.index[0]), str(df.index[-1])],
        "n_configs": len(configs),
        "run_seconds": round(total, 1),
        "fee_profiles": FEE_PROFILES,
    }
    with open(out_dir / "manifest.json", "w") as f:
        json.dump(manifest, f, indent=2)

    # Print top-10 to stdout for immediate eyeball
    print("\n" + "=" * 72)
    print(f"TOP 10 by Lighter score:")
    print("=" * 72)
    show = top.head(10).copy()
    print(show.to_string(index=False))


if __name__ == "__main__":
    main()
