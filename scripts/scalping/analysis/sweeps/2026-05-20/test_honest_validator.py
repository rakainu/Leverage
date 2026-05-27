"""Regression guard for the HONEST validator (2026-05-27).

Two optimism bugs once inflated this engine's ZEC 180d net from a realistic
-$7.8k to a fantasy +$29.3k (see memory project_v3_entry_fill_phantom):
  1. entry filled at the exact EMA(9) — an unfillable limit at the retest dip.
  2. protective stops (BE/lock/trail) advanced off the bar HIGH — the live
     bridge samples the order-book mid every 5s and never sees that wick.

This test pins the corrected behavior so neither bug can silently return.

Run:  python test_honest_validator.py
Part A (unit) needs no network. Part B (calibration) fetches/uses cached ZEC data.
"""
from __future__ import annotations
import sys

from strategy import TrailParams, EntryFilters, simulate_trade, run_backtest, kpis

P = TrailParams(margin_usdt=250, leverage=30, sl_loss_usdt=80, breakeven_usdt=15,
                lock_profit_activate_usdt=25, lock_profit_usdt=20,
                trail_activate_usdt=40, trail_start_usdt=45, trail_distance_usdt=15,
                commission_pct=0.0)

passed = failed = 0
def check(name, cond):
    global passed, failed
    if cond:
        passed += 1; print(f"  PASS  {name}")
    else:
        failed += 1; print(f"  FAIL  {name}")


def part_a_unit():
    """A bar that wicks favorably then reverses to the stop. With 'extreme' the
    SM arms protection off the wick (small locked win); with 'close' it never
    sees the wick and eats the full stop. Deterministic — no network."""
    print("Part A — unit: fav_mode changes protection arming")
    entry = 100.0
    # bar1: spikes to 100.5 (+$37.5 peak, would arm BE+lock) but closes flat at 100.0
    # bar2: drops to 98.5, below the initial SL (98.933)
    bars = [(0, 100.0, 100.5, 100.0, 100.0),
            (1, 100.0, 100.0,  98.5,  98.5)]

    r_ext = simulate_trade("long", entry, bars, P, ordering="avg", fav_mode="extreme")
    r_cls = simulate_trade("long", entry, bars, P, ordering="avg", fav_mode="close")

    check("extreme arms protection -> trail_sl (locked win)",
          r_ext.exit_reason == "trail_sl" and r_ext.pnl_usdt > 0)
    check("close never arms -> full 'sl' loss",
          r_cls.exit_reason == "sl" and r_cls.pnl_usdt < 0)
    check("extreme strictly better than close on identical bars",
          r_ext.pnl_usdt > r_cls.pnl_usdt)


def part_b_calibration():
    """Pin the honest ZEC 180d numbers and the documented fantasy."""
    print("\nPart B — calibration: honest defaults vs the documented lie (ZEC 180d)")
    try:
        from engine import load_symbol
        from strategy import prepare_dataframe
        df = prepare_dataframe(load_symbol("ZEC", "5m", days_back=180))
    except Exception as exc:
        print(f"  SKIP  (data unavailable: {exc})")
        return
    F = EntryFilters(block_weekdays={6}, min_abs_slope_pct=0.12, block_body_band=(0.3, 0.6))

    _, t_lie = run_backtest(df, P, filters=F, entry_mode="ema", fav_advance="extreme")
    _, t_hon = run_backtest(df, P, filters=F)  # HONEST defaults
    k_lie, k_hon = kpis(t_lie), kpis(t_hon)
    print(f"  lie    : n={k_lie['n']} WR={k_lie['win_rate']:.0%} "
          f"SL={(t_lie.exit_reason=='sl').mean():.0%} net=${k_lie['net_pnl']:,.0f}")
    print(f"  honest : n={k_hon['n']} WR={k_hon['win_rate']:.0%} "
          f"SL={(t_hon.exit_reason=='sl').mean():.0%} net=${k_hon['net_pnl']:,.0f}")

    # The lie must still reproduce a wildly profitable fantasy (proves modes work)
    check("documented lie reproduces fantasy (net > +$20k)", k_lie["net_pnl"] > 20000)
    check("lie full-SL rate is unrealistically low (< 8%)",
          (t_lie.exit_reason == "sl").mean() < 0.08)

    # ANTI-FANTASY GUARDS — honest model must NOT look like the lie
    check("honest model is NOT wildly profitable (net < +$1k)", k_hon["net_pnl"] < 1000)
    check("honest avg/trade is not a phantom edge (< +$1.0)", k_hon["avg_trade"] < 1.0)
    check("honest full-SL rate is realistic vs live ~36% (>= 20%)",
          (t_hon.exit_reason == "sl").mean() >= 0.20)
    # The gap between lie and honest is the whole point
    check("lie overstates net by > $25k vs honest",
          k_lie["net_pnl"] - k_hon["net_pnl"] > 25000)


if __name__ == "__main__":
    part_a_unit()
    part_b_calibration()
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
