"""Stage-1b: test pullback-in-trend (buy-the-dip momentum) + re-rank ALL families
on (a) the full basket and (b) the trender subset (ZEC/DOGE/AVAX/HYPE/SOL) where
stage-1 showed momentum has signal. Last honest look before A->B decision."""
from __future__ import annotations
import itertools
from common import available_coins, load, run_family, portfolio, weeks_span
from momentum_lib import pullback_trend, donchian, ema_momentum

TRENDERS = ["ZEC", "DOGE", "AVAX", "HYPE", "SOL"]

GRID = {
    "pullback_trend": (pullback_trend, dict(
        fast=[10, 20], slow=[50, 100], slope_lb=[20], sl_atr=[1.0, 1.5],
        trail_atr=[2.0, 3.0], adx_min=[0, 20], max_bars=[48, 96])),
    "donchian": (donchian, dict(
        lookback=[40, 60], sl_atr=[1.5], trail_atr=[3.0], adx_min=[0, 25], max_bars=[48, 96])),
    "ema_momentum": (ema_momentum, dict(
        fast=[20], slow=[40], fresh=[10], sl_atr=[1.5], trail_atr=[2.5, 3.5],
        adx_min=[0, 25], max_bars=[48, 96])),
}


def combos(grid):
    keys = list(grid)
    for vals in itertools.product(*[grid[k] for k in keys]):
        yield dict(zip(keys, vals))


def rank(dfs, wk, label):
    rows = []
    for fam, (fn, grid) in GRID.items():
        for p in combos(grid):
            per = run_family(fn, dfs, dict(side="both", **p))
            m = portfolio(per)
            if m is None or m["n"] < 25:
                continue
            rows.append((fam, p, m))
    rows.sort(key=lambda r: r[2]["avg_r"], reverse=True)
    need = max(2, int(0.6 * len(dfs)))
    print(f"\n===== {label} ({len(dfs)} coins, ~{wk:.0f}wk) — top 12 by avgR =====")
    print(f"{'family':15} {'avgR':>6} {'PF':>5} {'WR':>4} {'net%':>7} {'maxDD':>6} {'t':>5} {'n':>5} {'t/wk':>5} {'cz+':>5}")
    for fam, p, m in rows[:12]:
        flag = "  <<" if (m["coins_pos"] >= need and m["avg_r"] > 0) else ""
        print(f"{fam:15} {m['avg_r']:+.3f} {m['pf']:5.2f} {m['wr']:3.0f}% {m['net_pct']:+7.0f} "
              f"{m['max_dd']:5.1f}% {m['t']:+.2f} {m['n']:5} {m['n']/wk:5.1f} {m['coins_pos']}/{m['ncoins']}{flag}")
    return rows


def main():
    coins = available_coins()
    full = {c: load(c) for c in coins}
    trend = {c: full[c] for c in TRENDERS if c in full}
    wk = weeks_span(full)
    print(f"# Stage-1b | full={coins} | trenders={list(trend)} | 15m Lighter 0-fee")
    rank(full, wk, "FULL BASKET")
    rank(trend, weeks_span(trend), "TRENDER SUBSET")


if __name__ == "__main__":
    main()
