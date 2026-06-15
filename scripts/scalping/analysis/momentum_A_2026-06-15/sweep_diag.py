"""Diagnostic: rank ALL stage-1 configs by expectancy regardless of pass/fail, and
show per-coin net for the best of each family — to understand the failure mode
(is momentum net-negative everywhere, or carried by a few volatile coins?)."""
from __future__ import annotations
import itertools
from common import available_coins, load, run_family, portfolio, weeks_span
from momentum_lib import donchian, vol_expansion, roc_momentum, ema_momentum
from sweep_stage1 import GRID, combos


def main():
    coins = available_coins()
    dfs = {c: load(c) for c in coins}
    wk = weeks_span(dfs)
    print(f"# DIAG | coins={coins} | ~{wk:.0f}wk | 15m | Lighter 0-fee\n")
    allrows = []
    best_per_fam = {}
    for fam, (fn, grid) in GRID.items():
        fam_rows = []
        for p in combos(grid):
            per = run_family(fn, dfs, dict(side="both", **p))
            m = portfolio(per)
            if m is None or m["n"] < 30:
                continue
            allrows.append((fam, p, m, per))
            fam_rows.append((fam, p, m, per))
        if fam_rows:
            best_per_fam[fam] = max(fam_rows, key=lambda r: r[2]["avg_r"])

    allrows.sort(key=lambda r: r[2]["avg_r"], reverse=True)
    print("=== TOP 15 configs by avgR (any) ===")
    print(f"{'family':14} {'avgR':>6} {'PF':>5} {'WR':>4} {'net%':>7} {'maxDD':>6} {'t':>5} {'n':>5} {'cz+':>4}")
    for fam, p, m, _ in allrows[:15]:
        print(f"{fam:14} {m['avg_r']:+.3f} {m['pf']:5.2f} {m['wr']:3.0f}% {m['net_pct']:+7.0f} "
              f"{m['max_dd']:5.1f}% {m['t']:+.2f} {m['n']:5} {m['coins_pos']}/{m['ncoins']}")

    print("\n=== best config of each family — per-coin net R ===")
    for fam, (f2, p, m, per) in best_per_fam.items():
        ps = " ".join(f"{k}={v}" for k, v in p.items() if k != "max_bars")
        print(f"\n{fam}: avgR={m['avg_r']:+.3f} PF={m['pf']:.2f} net={m['net_pct']:+.0f}% | {ps}")
        for c in coins:
            netR = sum(t.r_multiple for t in per[c])
            n = len(per[c])
            wr = (sum(1 for t in per[c] if t.pnl_usd > 0) / n * 100) if n else 0
            print(f"    {c:5} n={n:4} netR={netR:+7.1f} wr={wr:3.0f}%")


if __name__ == "__main__":
    main()
