"""Validate local engine against the 2026-05-15 PineLab sweep numbers.

Target (from commit 2627a68, ZEC 6mo / 52k bars):
  Baseline (SL=$32.50, no filters):  net=+$1,508  PF=1.03  WR=49%  DD=-$3,638
  F8 (slope>=0.15 + no-Sun + body 0.3-0.5 + SL=$82.50):
                                     net=+$13,834 PF=3.10  WR=72%  DD=-$262

Acceptance: net PnL within ±20% of target.  PF and DD same sign and order of magnitude.
(Exact match unlikely — different data source, possible slight Pine differences.)
"""
from __future__ import annotations

from dataclasses import replace
import time

from engine import load_symbol
from strategy import (
    TrailParams, EntryFilters, prepare_dataframe, run_backtest, kpis,
)


TARGETS = {
    "baseline_sl32.50_no_filters": {
        "net_pnl": 1508, "profit_factor": 1.03, "max_dd": -3638, "win_rate": 0.49,
    },
    "F8_slope0.15_noSun_body03-05_sl82.50": {
        "net_pnl": 13834, "profit_factor": 3.10, "max_dd": -262, "win_rate": 0.72,
    },
}


def fmt_drift(label: str, got: float, target: float) -> str:
    if target == 0:
        return f"{label}: got {got:.2f}  target {target:.2f}"
    pct = (got - target) / abs(target) * 100
    return f"{label}: got {got:.2f}  target {target:.2f}  drift {pct:+.1f}%"


def main():
    print("=" * 76)
    print("CALIBRATION — local engine vs 2026-05-15 PineLab numbers (ZEC 6mo)")
    print("=" * 76)

    t0 = time.time()
    df = load_symbol("ZEC", "5m", days_back=180)
    df = prepare_dataframe(df)
    print(f"\nBars: {len(df)}  window: {df.index[0]} -> {df.index[-1]}")
    print(f"Pine signals: buy={int(df['buy_sig'].sum())}  sell={int(df['sell_sig'].sum())}")
    print(f"prep time: {time.time() - t0:.1f}s")

    # --- Run 1: baseline ---
    print("\n--- Run 1: baseline (SL=$32.50, NO entry filters) ---")
    p1 = replace(TrailParams(), sl_loss_usdt=32.50)
    t0 = time.time()
    _, tdf1 = run_backtest(df, p1)
    k1 = kpis(tdf1)
    print(f"  bt time: {time.time() - t0:.1f}s  n={k1['n']}")
    tgt = TARGETS["baseline_sl32.50_no_filters"]
    print("  " + fmt_drift("net_pnl       ", k1["net_pnl"], tgt["net_pnl"]))
    print("  " + fmt_drift("profit_factor ", k1["profit_factor"], tgt["profit_factor"]))
    print("  " + fmt_drift("max_dd        ", k1["max_dd"], tgt["max_dd"]))
    print("  " + fmt_drift("win_rate      ", k1["win_rate"], tgt["win_rate"]))

    # --- Run 2: F8 winner ---
    print("\n--- Run 2: F8 (slope>=0.15 + noSunday + body 0.3-0.5 + SL=$82.50) ---")
    p2 = replace(TrailParams(), sl_loss_usdt=82.50)
    f2 = EntryFilters(
        block_weekdays={6},  # Sunday
        min_abs_slope_pct=0.15,
        block_body_band=(0.3, 0.5),
    )
    t0 = time.time()
    _, tdf2 = run_backtest(df, p2, filters=f2)
    k2 = kpis(tdf2)
    print(f"  bt time: {time.time() - t0:.1f}s  n={k2['n']}")
    tgt = TARGETS["F8_slope0.15_noSun_body03-05_sl82.50"]
    print("  " + fmt_drift("net_pnl       ", k2["net_pnl"], tgt["net_pnl"]))
    print("  " + fmt_drift("profit_factor ", k2["profit_factor"], tgt["profit_factor"]))
    print("  " + fmt_drift("max_dd        ", k2["max_dd"], tgt["max_dd"]))
    print("  " + fmt_drift("win_rate      ", k2["win_rate"], tgt["win_rate"]))

    print("\n" + "=" * 76)
    print("CALIBRATION SUMMARY")
    print("=" * 76)
    print("Direction matches expected if:")
    print("  - Baseline net_pnl positive but small, PF ~1, DD large negative")
    print("  - F8 net_pnl much larger positive, PF >2, DD much smaller")
    print(f"\nBaseline: net=${k1['net_pnl']:+.0f}  PF={k1['profit_factor']:.2f}  DD=${k1['max_dd']:+.0f}")
    print(f"F8:       net=${k2['net_pnl']:+.0f}  PF={k2['profit_factor']:.2f}  DD=${k2['max_dd']:+.0f}")
    print()
    f8_lift = k2["net_pnl"] - k1["net_pnl"]
    pf_lift = k2["profit_factor"] - k1["profit_factor"]
    print(f"F8 lift over baseline: net=+${f8_lift:.0f}  PF=+{pf_lift:.2f}")
    print("(Original 2026-05-15: net lift ~+$12,326  PF lift ~+2.07)")


if __name__ == "__main__":
    main()
