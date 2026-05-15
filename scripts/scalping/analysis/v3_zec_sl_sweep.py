"""V3 ZEC SL sweep — find the best `sl_loss_usdt` baseline for scalping V3.

Setup:
  - Combines trade_log from V1/V2/V3 bridge DBs (pulled to ./data/).
  - Filters to ZEC-USDT, opened_at >= 2026-04-25 (slope-gate cutover).
    Entries pre-cutover came from a code path without the slope filter,
    so they over-represent the entries V3 would generate today.
  - Fixes BE/lock/trail at V3 baseline values; sweeps SL only.
  - Per-trade slippage drawn from each symbol's own historical SL fills.

Output:
  ./analysis/v3_zec_sl_sweep_<YYYY-MM-DD>.csv  (all rows)
  Ranked top-10 to stdout.

Reuses the simulator from optimal_params_sweep.py (same dir).

Run from repo root (or anywhere — paths are anchored to this file):
  python scripts/scalping/analysis/v3_zec_sl_sweep.py
"""
from __future__ import annotations

import csv
import os
import sqlite3
import sys
from datetime import datetime, timezone
from statistics import median
from typing import Iterable

# Make optimal_params_sweep importable (same dir as this script)
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from optimal_params_sweep import (
    TrailParams,
    simulate_trade,
    slippage_profile,
    fetch_bars,
    make_client,
    to_ms,
)

DATA_DIR = os.path.join(_HERE, "data")
DB_FILES = [
    os.path.join(DATA_DIR, "v1_bridge.db"),
    os.path.join(DATA_DIR, "v2_bridge.db"),
    os.path.join(DATA_DIR, "v3_bridge.db"),
]

SYMBOL = "ZEC-USDT"
FROM_DATE = "2026-04-25"  # slope-gate cutover

# V3 baseline (defaults block in blofin_bridge.v3.yaml). $100 margin reference.
V3_BASELINE = TrailParams(
    margin_usdt=100,
    leverage=30,
    sl_loss_usdt=13,              # ← the variable we sweep
    breakeven_usdt=12,
    lock_profit_activate_usdt=18,
    lock_profit_usdt=15,
    trail_activate_usdt=30,
    trail_start_usdt=32,
    trail_distance_usdt=15,
    tp_ceiling_pct=2.0,
)

# SL baseline values to sweep (gets multiplied by 2.5× margin_usdt scaling
# at ZEC's live $250 margin → effective range $20–$45 SL on a ZEC trade).
SL_VALUES = [8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18]


def load_filtered_trades() -> list[dict]:
    """Pull ZEC trades from all three DBs, filtered to post-slope-gate window."""
    trades: list[dict] = []
    for db_path in DB_FILES:
        if not os.path.exists(db_path):
            continue
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            """
            SELECT id, symbol, side, entry_price, exit_price, initial_sl,
                   trail_activated, trail_high_price, exit_reason,
                   pnl_usdt, opened_at, closed_at, duration_secs,
                   margin_usdt AS live_margin, leverage AS live_leverage
            FROM trade_log
            WHERE symbol = ?
              AND opened_at >= ?
              AND entry_price IS NOT NULL
              AND initial_sl IS NOT NULL
              AND initial_sl > 0
            ORDER BY opened_at
            """,
            (SYMBOL, FROM_DATE),
        )
        for r in cur.fetchall():
            t = dict(r)
            t["_db"] = os.path.basename(db_path)
            trades.append(t)
        conn.close()
    return trades


def gather_ohlcv(trades: list[dict], client) -> dict[tuple[str, int], list[list[float]]]:
    """Fetch 5m bars per trade — entry through max(actual close, entry + 24h)."""
    out: dict[tuple[str, int], list[list[float]]] = {}
    for t in trades:
        sym = t["symbol"]
        ccxt_sym = sym.replace("-", "/") + ":USDT"
        start_ms = to_ms(t["opened_at"])
        end_ms = to_ms(t["closed_at"])
        window_end = max(end_ms, start_ms + 24 * 60 * 60_000)
        bars = fetch_bars(client, ccxt_sym, start_ms - 60_000, window_end + 60_000)
        bars = [
            b for b in bars
            if start_ms - 5 * 60_000 <= b[0] <= start_ms + 24 * 60 * 60_000
        ]
        out[(t["_db"], t["id"])] = bars
    return out


def run_sl_sweep(
    trades: list[dict],
    bars_by_key: dict[tuple[str, int], list],
    slip_by_sym: dict[str, dict],
    sl_values: Iterable[int],
) -> list[dict]:
    """For each SL value, sim every trade. Returns one summary row per SL."""
    rows = []
    for sl in sl_values:
        pnls: list[float] = []
        sl_hits = be_hits = trail_exits = ceiling_hits = unresolved = 0
        for t in trades:
            bars = bars_by_key.get((t["_db"], t["id"])) or []
            if not bars:
                continue
            slip_pct = slip_by_sym.get(t["symbol"], {}).get("median_pct", 0.0)
            params = TrailParams(
                margin_usdt=V3_BASELINE.margin_usdt,
                leverage=V3_BASELINE.leverage,
                sl_loss_usdt=sl,
                breakeven_usdt=V3_BASELINE.breakeven_usdt,
                lock_profit_activate_usdt=V3_BASELINE.lock_profit_activate_usdt,
                lock_profit_usdt=V3_BASELINE.lock_profit_usdt,
                trail_activate_usdt=V3_BASELINE.trail_activate_usdt,
                trail_start_usdt=V3_BASELINE.trail_start_usdt,
                trail_distance_usdt=V3_BASELINE.trail_distance_usdt,
                tp_ceiling_pct=V3_BASELINE.tp_ceiling_pct,
                sl_slippage_pct=slip_pct,
            )
            # "avg" ordering (mean of fav_first + adv_first) — the same fairness
            # technique the original sweep uses to soften bar-level resolution.
            a = simulate_trade(t["side"], t["entry_price"], bars, params, "fav_first")
            b = simulate_trade(t["side"], t["entry_price"], bars, params, "adv_first")
            avg_pnl = (a.pnl_usdt + b.pnl_usdt) / 2
            # Pick exit reason from worse-outcome ordering (pessimistic)
            res = a if a.pnl_usdt <= b.pnl_usdt else b
            pnls.append(avg_pnl)
            if res.exit_reason == "sl":
                sl_hits += 1
            elif res.exit_reason == "sl_be":
                be_hits += 1
            elif res.exit_reason == "trail_sl":
                trail_exits += 1
            elif res.exit_reason == "tp_ceiling":
                ceiling_hits += 1
            elif res.exit_reason == "unresolved":
                unresolved += 1
        n = len(pnls)
        if n == 0:
            continue
        wins = sum(1 for p in pnls if p > 0)
        net = sum(pnls)
        running = peak = 0.0
        max_dd = 0.0
        for p in pnls:
            running += p
            peak = max(peak, running)
            max_dd = min(max_dd, running - peak)
        rows.append({
            "sl_baseline_usdt": sl,
            "sl_effective_zec_usdt": round(sl * 2.5, 2),  # ZEC runs at 2.5× scaling
            "n_trades": n,
            "wins": wins,
            "win_rate": round(wins / n, 3),
            "net_pnl_usdt": round(net, 2),
            "avg_pnl_usdt": round(net / n, 3),
            "median_pnl_usdt": round(median(pnls), 3),
            "max_dd_usdt": round(max_dd, 2),
            "sl_hits": sl_hits,
            "be_hits": be_hits,
            "trail_exits": trail_exits,
            "ceiling_hits": ceiling_hits,
            "unresolved": unresolved,
        })
    return rows


def calibrate_against_live(
    trades: list[dict],
    bars_by_key: dict,
    slip_by_sym: dict,
) -> None:
    """Compare V3-era trade outcomes (live actual) to sim with V3 baseline params.

    Only V3-era trades (opened in v3_bridge.db) had V3 exit params live,
    so only those provide a valid live-vs-sim check.
    """
    v3_trades = [t for t in trades if t["_db"] == "v3_bridge.db"]
    if not v3_trades:
        print("  (no V3-era trades — skipping calibration check)")
        return

    live_pnl = sum(t["pnl_usdt"] or 0 for t in v3_trades)
    live_wins = sum(1 for t in v3_trades if (t["pnl_usdt"] or 0) > 0)
    rows = run_sl_sweep(v3_trades, bars_by_key, slip_by_sym, [V3_BASELINE.sl_loss_usdt])
    if not rows:
        return
    sim = rows[0]
    diff = live_pnl - sim["net_pnl_usdt"]
    print(f"  V3-era trades:        n={len(v3_trades)}")
    print(f"  Live (actual):        net_pnl=${live_pnl:+.2f}  wins={live_wins}")
    print(f"  Sim @ V3 baseline:    net_pnl=${sim['net_pnl_usdt']:+.2f}  wins={sim['wins']}")
    print(f"  Offset (live - sim):  ${diff:+.2f}  (~${diff/len(v3_trades):+.3f}/trade)")


def main():
    print("=" * 72)
    print(f"V3 ZEC SL sweep — {datetime.now(timezone.utc).isoformat()}")
    print(f"  Symbol: {SYMBOL}    From: {FROM_DATE}    SL baseline grid: {SL_VALUES}")
    print(f"  Fixed at V3 baseline: BE={V3_BASELINE.breakeven_usdt}, "
          f"lock_act={V3_BASELINE.lock_profit_activate_usdt}, "
          f"lock={V3_BASELINE.lock_profit_usdt}, "
          f"trail_act={V3_BASELINE.trail_activate_usdt}, "
          f"trail_start={V3_BASELINE.trail_start_usdt}, "
          f"trail_dist={V3_BASELINE.trail_distance_usdt}")
    print("=" * 72)

    print("\n[1/5] Loading trades from V1+V2+V3 DBs...")
    trades = load_filtered_trades()
    print(f"  ZEC trades since {FROM_DATE}: {len(trades)}")
    by_db = {}
    for t in trades:
        by_db[t["_db"]] = by_db.get(t["_db"], 0) + 1
    print(f"  by db: {by_db}")

    print("\n[2/5] Slippage profile from real SL exits...")
    slip = slippage_profile(trades)
    for sym, info in slip.items():
        print(f"  {sym}: n={info['n']}  median={info['median_pct']*100:.4f}%  "
              f"p75={info['p75_pct']*100:.4f}%  p95={info['p95_pct']*100:.4f}%")

    print("\n[3/5] Fetching 5m OHLCV per trade (cached)...")
    client = make_client()
    bars_by_key = gather_ohlcv(trades, client)
    n_with_bars = sum(1 for v in bars_by_key.values() if v)
    print(f"  fetched bars for {n_with_bars}/{len(trades)} trades")

    print("\n[4/5] Calibration check (V3-era trades only)...")
    calibrate_against_live(trades, bars_by_key, slip)

    print("\n[5/5] Running SL sweep...")
    rows = run_sl_sweep(trades, bars_by_key, slip, SL_VALUES)
    rows.sort(key=lambda r: r["net_pnl_usdt"], reverse=True)

    out_csv = os.path.join(
        _HERE, f"v3_zec_sl_sweep_{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.csv"
    )
    with open(out_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    print("\n" + "=" * 72)
    print(f"Results ranked by net PnL (n_trades={rows[0]['n_trades']})")
    print("=" * 72)
    hdr = (f"{'rank':>4}  {'sl_base':>7}  {'sl_zec_$':>8}  {'net_pnl':>9}  "
           f"{'wr':>5}  {'avg':>7}  {'maxdd':>8}  {'sl':>4}  {'be':>4}  "
           f"{'trl':>4}  {'cap':>4}  {'unr':>4}")
    print(hdr)
    print("-" * len(hdr))
    for i, r in enumerate(rows, 1):
        print(
            f"{i:>4}  ${r['sl_baseline_usdt']:>6}  ${r['sl_effective_zec_usdt']:>7}  "
            f"${r['net_pnl_usdt']:>+8.2f}  {r['win_rate']:>5.3f}  "
            f"${r['avg_pnl_usdt']:>+6.2f}  ${r['max_dd_usdt']:>+7.2f}  "
            f"{r['sl_hits']:>4}  {r['be_hits']:>4}  {r['trail_exits']:>4}  "
            f"{r['ceiling_hits']:>4}  {r['unresolved']:>4}"
        )
    print("=" * 72)
    print(f"CSV: {out_csv}")


if __name__ == "__main__":
    main()
