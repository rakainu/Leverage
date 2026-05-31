"""Validate a MULTI-COIN basket strategy honestly. Runs the same config on every
coin, pools trades on a shared time axis, then splits the POOLED stream by time
for IS/OOS and walk-forward (the correct unit of analysis for a basket).

Reports per-coin edge, pooled edge, IS/OOS, 4 time-folds, slippage stress, and a
fixed-notional $ outcome with honest liquidation.

Usage: python basket_validate.py <family> <tf> '<json params>'  [coins csv]
"""
from __future__ import annotations
import sys, json
import numpy as np
import pandas as pd
import common as K
from btengine import simulate, metrics


def coin_trades(fam, coin, tf, params, costs):
    df = K.load(coin, tf)
    fn = K.SL.REGISTRY[fam]
    tr = simulate(df, fn(df, side="both", **params), costs, K.RISK, K.TF_MIN[tf])
    rows = []
    for t in tr:
        r = t.side * (t.exit_price - t.entry_price) / t.entry_price
        rows.append((t.entry_time, r, t.mae_frac, t.bars_held, coin))
    return rows, df


def edge(rows, tf, weeks):
    if not rows:
        return None
    rets = np.array([r[1] for r in rows])
    w = rets[rets > 0]; l = rets[rets < 0]
    pf = w.sum() / -l.sum() if l.sum() < 0 else float("inf")
    return dict(n=len(rets), tpw=len(rets) / weeks if weeks else 0,
                pf=pf, wr=100 * len(w) / len(rets), exp=100 * rets.mean(),
                avg_win=100 * w.mean() if len(w) else 0, avg_loss=100 * l.mean() if len(l) else 0,
                hold=np.mean([r[3] for r in rows]) * K.TF_MIN[tf], maxmae=100 * max(r[2] for r in rows))


def show(label, e):
    if not e:
        print(f"  {label:14} (no trades)"); return
    pf = "inf" if e["pf"] == float("inf") else f"{e['pf']:.2f}"
    print(f"  {label:14} n={e['n']:>5} tpw={e['tpw']:>6.1f} PF={pf:>5} WR={e['wr']:>4.0f}% "
          f"exp={e['exp']:>+6.3f}% win={e['avg_win']:>+5.2f}% loss={e['avg_loss']:>+5.2f}% "
          f"hold={e['hold']:>4.0f}m maxMAE={e['maxmae']:.1f}%", flush=True)


def main():
    fam, tf = sys.argv[1], sys.argv[2]
    params = json.loads(sys.argv[3])
    coins = sys.argv[4].split(",") if len(sys.argv) > 4 else K.COINS

    # full period weeks from one coin's frame
    sample_df = K.load(coins[0], tf)
    weeks = K.weeks_of(sample_df, tf)
    print(f"\n### BASKET {fam} | {tf} | {coins} | {params}")
    print(f"period ~{sample_df.index[0].date()} -> {sample_df.index[-1].date()} ({weeks:.1f} wk)")

    # per-coin + pooled (Lighter zero-fee)
    pooled = []
    print("PER-COIN EDGE (Lighter zero-fee):")
    for c in coins:
        rows, df = coin_trades(fam, c, tf, params, K.LIGHTER)
        pooled += rows
        show(c, edge(rows, tf, K.weeks_of(df, tf)))
    pooled.sort(key=lambda r: r[0])
    print("POOLED:")
    show("pooled-full", edge(pooled, tf, weeks))

    # time-based IS/OOS on pooled stream
    times = [r[0] for r in pooled]
    cut = times[int(len(times) * 0.70)]
    is_rows = [r for r in pooled if r[0] < cut]; oos_rows = [r for r in pooled if r[0] >= cut]
    show("pooled-IS70", edge(is_rows, tf, weeks * 0.7))
    show("pooled-OOS30", edge(oos_rows, tf, weeks * 0.3))

    # 4 time-folds
    print("WALK-FORWARD (4 equal time folds, pooled):")
    t0, t1 = pooled[0][0], pooled[-1][0]
    bounds = pd.date_range(t0, t1, periods=5)
    for k in range(4):
        seg = [r for r in pooled if bounds[k] <= r[0] < bounds[k + 1]]
        show(f"fold{k+1}", edge(seg, tf, weeks / 4))

    # slippage stress (rebuild pooled under hi-slip)
    print("SLIPPAGE STRESS (0.10%):")
    pooled_hi = []
    for c in coins:
        rows, _ = coin_trades(fam, c, tf, params, K.LIGHTER_HISLIP)
        pooled_hi += rows
    show("pooled hi-slip", edge(pooled_hi, tf, weeks))

    # fixed-notional $ outcome on pooled-full
    print("FIXED NOTIONAL ($ outcome, honest liquidation, pooled):")
    rets = np.array([r[1] for r in pooled]); mae = np.array([r[2] for r in pooled])
    print(f"  {'notl':>5}{'lev':>5}{'net$':>9}{'maxDD$':>9}{'liq':>5}{'liqDist%':>9}")
    for N in (100, 200, 250):
        for L in (10, 20, 30):
            liq_frac = (1.0 / L) * (1 - 0.005); margin = N / L
            pnl = np.where(mae >= liq_frac, -margin, rets * N)
            eq = np.cumsum(pnl); peak = np.maximum.accumulate(np.concatenate([[0.0], eq]))
            dd = (peak - np.concatenate([[0.0], eq])).max()
            print(f"  {N:>5}{L:>5}{pnl.sum():>9.0f}{dd:>9.0f}{int((mae>=liq_frac).sum()):>5}{100*liq_frac:>9.2f}", flush=True)


if __name__ == "__main__":
    main()
