"""Stage-1 triage: coarse grid over all 4 momentum families x params, 15m basket,
Lighter zero-fee, full window. Rank by honest portfolio expectancy, REQUIRING
generalization (positive on a majority of coins) and enough frequency. Top configs
graduate to stage-2 validation."""
from __future__ import annotations
import itertools
from common import available_coins, load, run_family, portfolio, weeks_span, LIGHTER
from momentum_lib import donchian, vol_expansion, roc_momentum, ema_momentum

GRID = {
    "donchian": (donchian, dict(
        lookback=[20, 40, 60], sl_atr=[1.0, 1.5], trail_atr=[2.0, 3.0],
        adx_min=[0, 20], max_bars=[48])),
    "vol_expansion": (vol_expansion, dict(
        lookback=[15, 25], contract_q=[0.6, 0.8], sl_atr=[1.5], trail_atr=[2.5, 3.5],
        adx_min=[0, 20], max_bars=[48])),
    "roc_momentum": (roc_momentum, dict(
        roc_p=[6, 10], z_thr=[1.5, 2.0], sl_atr=[1.5], trail_atr=[2.5], adx_min=[0],
        max_bars=[48])),
    "ema_momentum": (ema_momentum, dict(
        fast=[10, 20], slow=[40, 50], fresh=[10], sl_atr=[1.5], trail_atr=[2.5, 3.5],
        adx_min=[0, 20], max_bars=[48])),
}


def combos(grid):
    keys = list(grid)
    for vals in itertools.product(*[grid[k] for k in keys]):
        yield dict(zip(keys, vals))


def main():
    coins = available_coins()
    dfs = {c: load(c) for c in coins}
    wk = weeks_span(dfs)
    print(f"# Stage-1 triage | coins={coins} | ~{wk:.0f}wk | 15m | Lighter 0-fee | risk1% compound\n")
    rows = []
    for fam, (fn, grid) in GRID.items():
        for p in combos(grid):
            per = run_family(fn, dfs, dict(side="both", **p))
            m = portfolio(per)
            if m is None or m["n"] < 30:
                continue
            rows.append((fam, p, m))
    # rank: require generalization (>=60% coins positive) + positive expectancy, then by avg_r
    need = max(2, int(0.6 * len(coins)))
    good = [r for r in rows if r[2]["coins_pos"] >= need and r[2]["avg_r"] > 0]
    good.sort(key=lambda r: r[2]["avg_r"], reverse=True)
    print(f"Tested {len(rows)} configs; {len(good)} generalize (>= {need}/{len(coins)} coins +).\n")
    print(f"{'family':14} {'avgR':>6} {'PF':>5} {'WR':>4} {'net%':>7} {'maxDD':>6} "
          f"{'t':>5} {'n':>5} {'t/wk':>5} {'cz+':>4}  params")
    for fam, p, m in good[:25]:
        ps = " ".join(f"{k}={v}" for k, v in p.items() if k not in ("max_bars",))
        print(f"{fam:14} {m['avg_r']:+.3f} {m['pf']:5.2f} {m['wr']:3.0f}% {m['net_pct']:+7.0f} "
              f"{m['max_dd']:5.1f}% {m['t']:+.2f} {m['n']:5} {m['n']/wk:5.1f} "
              f"{m['coins_pos']}/{m['ncoins']:<2} {ps}")
    if not good:
        print("No generalizing momentum config found in stage-1 grid.")


if __name__ == "__main__":
    main()
