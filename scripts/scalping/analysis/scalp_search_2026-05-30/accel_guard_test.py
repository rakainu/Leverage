"""Tail-fix test: does an acceleration guard cut the scalper's news-rip losses?

The live scalper (regime_mr, sl_atr=2.0, tp_frac=0.3) is ~80% WR but barely +EV
because the rare loss is a FULL 2-ATR stop — it fades z-extensions and gets run
over when the extension is a news-driven acceleration. accel_mult>0 declines to
fade a bar whose range >= accel_mult*ATR (a volatility-climax bar).

Compares baseline vs accel guard at several thresholds, per coin + pooled, on
the LIVE config params. Reports PF/net/WR/DD/trades AND the tail (worst loss,
sum of the worst 10% of losses) — the thing we're actually trying to shrink.

Run: python accel_guard_test.py
"""
import numpy as np
import pandas as pd
from common import load, run, LIGHTER, COINS

LIVE = dict(trend_len=200, slope_lb=20, z_period=30, z_entry=1.5,
            sl_atr=2.0, tp_frac=0.3, max_bars=12, limit_atr=0.25, atr_p=14)
TF = "15m"
GUARDS = [0.0, 3.5, 3.0, 2.5, 2.0]   # 0 = baseline (off)


def tail_stats(trades):
    losses = sorted([t.pnl_usd for t in trades if t.pnl_usd < 0])
    if not losses:
        return 0.0, 0.0, 0
    k = max(1, len(losses) // 10)
    worst10 = sum(losses[:k])
    return losses[0], worst10, len(losses)


def main():
    print(f"regime_mr LIVE config, {TF}, zero-fee Lighter costs, both sides\n")
    print(f"{'accel':>6} {'n':>5} {'PF':>6} {'WR%':>5} {'net%':>7} {'DD%':>6} "
          f"{'avgW$':>7} {'avgL$':>7} {'worst$':>8} {'worst10%$':>10}")
    pooled = {}
    for g in GUARDS:
        params = dict(LIVE)
        if g > 0:
            params["accel_mult"] = g
        agg = dict(n=0, net=0.0, wins=0, dd=[], wsum=0.0, lsum=0.0, wn=0, ln=0,
                   worst=0.0, worst10=0.0, alltr=[])
        for coin in COINS:
            df = load(coin, TF)
            m, trades = run("regime_mr", df, TF, "both", params, costs=LIGHTER)
            agg["n"] += m["n"]; agg["wins"] += round(m["win_rate"] / 100 * m["n"])
            agg["dd"].append(m["max_dd_pct"])
            for t in trades:
                if t.pnl_usd > 0:
                    agg["wsum"] += t.pnl_usd; agg["wn"] += 1
                else:
                    agg["lsum"] += t.pnl_usd; agg["ln"] += 1
            agg["net"] += m["net_pct"]
            agg["alltr"].extend(trades)
        worst, worst10, _ = tail_stats(agg["alltr"])
        pf = agg["wsum"] / -agg["lsum"] if agg["lsum"] < 0 else float("inf")
        wr = agg["wins"] / agg["n"] * 100 if agg["n"] else 0
        avgw = agg["wsum"] / agg["wn"] if agg["wn"] else 0
        avgl = agg["lsum"] / agg["ln"] if agg["ln"] else 0
        pooled[g] = dict(n=agg["n"], pf=pf, net=agg["net"], worst10=worst10)
        lbl = "OFF" if g == 0 else f"{g:.1f}"
        print(f"{lbl:>6} {agg['n']:>5} {pf:>6.2f} {wr:>5.0f} {agg['net']:>+7.1f} "
              f"{np.mean(agg['dd']):>6.1f} {avgw:>7.1f} {avgl:>7.1f} {worst:>8.1f} "
              f"{worst10:>10.0f}")

    print("\n--- per-coin: baseline PF vs best-guard PF ---")
    best_g = max([g for g in GUARDS if g > 0], key=lambda g: pooled[g]["pf"])
    print(f"(best pooled guard = {best_g} ATR)")
    print(f"{'coin':5} {'base PF':>8} {'base net%':>10} {'guard PF':>9} {'guard net%':>11} {'base n':>7} {'g n':>5}")
    for coin in COINS:
        df = load(coin, TF)
        mb, _ = run("regime_mr", df, TF, "both", LIVE, costs=LIGHTER)
        pg = dict(LIVE); pg["accel_mult"] = best_g
        mg, _ = run("regime_mr", df, TF, "both", pg, costs=LIGHTER)
        pfb = mb["profit_factor"]; pfg = mg["profit_factor"]
        print(f"{coin:5} {pfb:>8.2f} {mb['net_pct']:>+10.1f} {pfg:>9.2f} "
              f"{mg['net_pct']:>+11.1f} {mb['n']:>7} {mg['n']:>5}")
    print("\nread: guard helps if PF + net rise while trades stay healthy and the "
          "worst10% tail shrinks. if net drops with PF, it's cutting winners too.")


if __name__ == "__main__":
    main()
